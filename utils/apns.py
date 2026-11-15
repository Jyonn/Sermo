"""Explicitly configured APNs transport; importing this module enables nothing."""
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx
import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class APNsUnavailable(Exception):
    """Safe configuration error, never including credentials or provider text."""


class _RedactAPNs(logging.Filter):
    def filter(self, record):
        if record.name.startswith('hpack.'):
            return False
        message = record.getMessage()
        message = re.sub(r'(/3/device/)[0-9a-fA-F]+', r'\1[redacted]', message)
        message = re.sub(r'(?i)bearer\s+[A-Za-z0-9_.-]+', 'bearer [redacted]', message)
        record.msg, record.args = message, ()
        return True


def _protect_transport_logs():
    # HTTPX's normal INFO request log includes the device token in the URL.
    for name in ('httpx', 'httpcore.http2', 'httpcore.http11', 'httpcore.connection', 'hpack.hpack'):
        logger = logging.getLogger(name)
        if not any(isinstance(value, _RedactAPNs) for value in logger.filters):
            logger.addFilter(_RedactAPNs())


@dataclass(frozen=True)
class APNsConfiguration:
    enabled: bool = False
    team_id: str = ''
    key_id: str = ''
    topic: str = ''
    environments: tuple = ()
    private_key: str = field(default='', repr=False)

    @classmethod
    def from_environment(cls, values):
        # A server process must explicitly opt in. Do not interpret truthy strings.
        return cls(enabled=values.get('FRIENDEN_APNS_ENABLED') == '1',
                   team_id=values.get('FRIENDEN_APNS_TEAM_ID', ''),
                   key_id=values.get('FRIENDEN_APNS_KEY_ID', ''),
                   topic=values.get('FRIENDEN_APNS_TOPIC', ''),
                   environments=tuple(filter(None, values.get('FRIENDEN_APNS_ENVIRONMENTS', '').split(','))),
                   private_key=values.get('FRIENDEN_APNS_PRIVATE_KEY', ''))


@dataclass(frozen=True)
class APNsResult:
    # accepted means APNs accepted the request, not that a device received it.
    outcome: str
    invalidated_at_ms: int | None = None


class APNsTransport:
    HOSTS = {'sandbox': 'https://api.sandbox.push.apple.com', 'production': 'https://api.push.apple.com'}
    TOKEN_SECONDS = 50 * 60

    def __init__(self, configuration, *, transport=None):
        self.configuration = configuration
        if configuration.enabled is not True:
            raise APNsUnavailable('disabled')
        if (not isinstance(configuration.environments, tuple) or not configuration.environments
                or any(not isinstance(value, str) or value not in self.HOSTS for value in configuration.environments)
                or not isinstance(configuration.team_id, str) or not re.fullmatch(r'[A-Z0-9]{10}', configuration.team_id)
                or not isinstance(configuration.key_id, str) or not re.fullmatch(r'[A-Z0-9]{10}', configuration.key_id)
                or not isinstance(configuration.topic, str) or not re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', configuration.topic)):
            raise APNsUnavailable('invalid_configuration')
        try:
            self._key = serialization.load_pem_private_key(configuration.private_key.encode('ascii'), password=None)
            if not isinstance(self._key, ec.EllipticCurvePrivateKey) or not isinstance(self._key.curve, ec.SECP256R1):
                raise ValueError('wrong key type')
        except (ValueError, TypeError, AttributeError, UnicodeError, UnsupportedAlgorithm):
            raise APNsUnavailable('invalid_signing_key') from None
        self._lock = threading.Lock()
        self._issued_at = 0
        self._authorization = None
        _protect_transport_logs()
        self._client = httpx.Client(http2=True, http1=False, timeout=httpx.Timeout(8, connect=5),
                                    follow_redirects=False, trust_env=False, transport=transport)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _provider_token(self):
        with self._lock:
            now = int(time.time())
            if self._authorization is not None and now < self._issued_at:
                raise APNsUnavailable('clock_changed')
            if self._authorization is None or now - self._issued_at >= self.TOKEN_SECONDS:
                self._authorization = jwt.encode({'iss': self.configuration.team_id, 'iat': now}, self._key,
                                                  algorithm='ES256', headers={'kid': self.configuration.key_id})
                self._issued_at = now
            return self._authorization

    def send(self, *, environment, token, payload, notification_id):
        if not isinstance(environment, str) or environment not in self.configuration.environments:
            raise ValueError('unconfigured environment')
        if not isinstance(token, str) or not 2 <= len(token) <= 1024 or len(token) % 2 or not re.fullmatch('[0-9a-fA-F]+', token):
            raise ValueError('invalid device token')
        if not isinstance(notification_id, uuid.UUID):
            raise ValueError('invalid notification identity')
        if not isinstance(payload, dict) or not isinstance(payload.get('aps'), dict) or 'alert' not in payload['aps']:
            raise ValueError('invalid alert payload')
        try:
            data = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
        except (ValueError, TypeError, UnicodeError):
            raise ValueError('invalid alert payload') from None
        if len(data) > 4096:
            raise ValueError('payload too large')
        headers = {'authorization': f'bearer {self._provider_token()}', 'apns-topic': self.configuration.topic,
                   'apns-push-type': 'alert', 'apns-priority': '10', 'apns-expiration': '0',
                   'apns-id': str(notification_id), 'content-type': 'application/json'}
        try:
            with self._client.stream('POST', f'{self.HOSTS[environment]}/3/device/{token.lower()}',
                                     headers=headers, content=data) as response:
                if response.http_version != 'HTTP/2' or response.headers.get('apns-id', str(notification_id)) != str(notification_id):
                    return APNsResult('unknown')
                if response.status_code == 200:
                    return APNsResult('accepted')
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > 8192:
                        return APNsResult('unknown')
                    body.extend(chunk)
                try:
                    error = json.loads(body)
                except (ValueError, UnicodeError):
                    error = None
                if response.status_code == 410 and isinstance(error, dict):
                    timestamp = error.get('timestamp')
                    if error.get('reason') in ('Unregistered', 'ExpiredToken') and type(timestamp) is int and timestamp > 0:
                        return APNsResult('rejected', invalidated_at_ms=timestamp)
                return APNsResult('rejected')
        except httpx.HTTPError:
            # Even a timeout may happen after acceptance. Never automatically retry.
            return APNsResult('unknown')
