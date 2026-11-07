import json
import logging
import uuid
from dataclasses import replace
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from django.test import SimpleTestCase

from utils.apns import APNsConfiguration, APNsResult, APNsTransport, APNsUnavailable


class APNsTransportTests(SimpleTestCase):
    def setUp(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()).decode('ascii')
        self.config = APNsConfiguration(enabled=True, team_id='ABCDE12345', key_id='12345ABCDE',
                                       topic='space.jyonn.Frienden', environments=('sandbox',), private_key=self.pem)
        self.requests = []
        self.response = httpx.Response(200, extensions={'http_version': b'HTTP/2'})
        self.notification = uuid.uuid4()

    def handler(self, request):
        self.requests.append(request)
        return self.response

    def transport(self, configuration=None, handler=None):
        value = APNsTransport(configuration or self.config, transport=httpx.MockTransport(handler or self.handler))
        self.addCleanup(value.close)
        return value

    def send(self, transport, **changes):
        values = dict(environment='sandbox', token='AB' * 32, payload={'aps': {'alert': 'Hello'}},
                      notification_id=self.notification)
        values.update(changes)
        return transport.send(**values)

    def test_disabled_and_partial_configuration_never_construct_network_client(self):
        with patch('utils.apns.httpx.Client') as client:
            for configuration in (APNsConfiguration(), replace(self.config, enabled=False),
                                  replace(self.config, enabled='true'), replace(self.config, environments=()),
                                  replace(self.config, environments=('https://evil.invalid',)),
                                  replace(self.config, environments=([],)), replace(self.config, team_id='invalid'),
                                  replace(self.config, key_id='key\n'), replace(self.config, topic='bad\nheader')):
                with self.assertRaises(APNsUnavailable):
                    APNsTransport(configuration)
            client.assert_not_called()

    def test_signing_key_validation_and_repr_are_safe(self):
        wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode('ascii')
        for key in ('sensitive-invalid-key', wrong, self.pem.replace('PRIVATE KEY', 'INVALID KEY')):
            with self.assertRaises(APNsUnavailable) as error:
                self.transport(replace(self.config, private_key=key))
            self.assertNotIn(key, str(error.exception))
        self.assertNotIn(self.pem, repr(self.config))

    def test_environment_loader_is_explicit_and_disabled_by_default(self):
        for enabled in ('true', 'yes', '0', '', None):
            self.assertFalse(APNsConfiguration.from_environment({'FRIENDEN_APNS_ENABLED': enabled}).enabled)
        config = APNsConfiguration.from_environment({'FRIENDEN_APNS_ENABLED': '1',
                                                      'FRIENDEN_APNS_ENVIRONMENTS': 'sandbox,production'})
        self.assertTrue(config.enabled)
        self.assertEqual(config.environments, ('sandbox', 'production'))

    def test_http2_client_has_no_proxy_redirect_or_http1_fallback(self):
        with patch('utils.apns.httpx.Client') as client:
            value = APNsTransport(self.config)
            kwargs = client.call_args.kwargs
            self.assertEqual({key: kwargs[key] for key in ('http2', 'http1', 'follow_redirects', 'trust_env')},
                             dict(http2=True, http1=False, follow_redirects=False, trust_env=False))
            self.assertEqual(kwargs['timeout'].connect, 5)
            self.assertEqual(kwargs['timeout'].read, 8)
            value.close()
            client.return_value.close.assert_called_once()

    def test_request_has_fixed_host_versioned_headers_and_verifiable_signature(self):
        transport = self.transport()
        with patch('utils.apns.time.time', return_value=1800000000):
            self.assertEqual(self.send(transport), APNsResult('accepted'))
        request = self.requests[0]
        self.assertEqual(str(request.url), 'https://api.sandbox.push.apple.com/3/device/' + 'ab' * 32)
        self.assertEqual(request.method, 'POST')
        for header, value in {'apns-push-type': 'alert', 'apns-priority': '10', 'apns-expiration': '0',
                              'apns-id': str(self.notification), 'apns-topic': self.config.topic}.items():
            self.assertEqual(request.headers[header], value)
        token = request.headers['authorization'].split()[1]
        self.assertEqual(jwt.get_unverified_header(token)['kid'], self.config.key_id)
        claims = jwt.decode(token, self.key.public_key(), algorithms=['ES256'], options={'verify_iat': False})
        self.assertEqual(claims, {'iss': self.config.team_id, 'iat': 1800000000})
        self.assertEqual(json.loads(request.content), {'aps': {'alert': 'Hello'}})

    def test_provider_token_is_reused_then_refreshed_and_clock_reversal_is_rejected(self):
        transport = self.transport()
        with patch('utils.apns.time.time', return_value=1800000000):
            self.send(transport)
        with patch('utils.apns.time.time', return_value=1800002999):
            self.send(transport)
        with patch('utils.apns.time.time', return_value=1800003000):
            self.send(transport)
        headers = [request.headers['authorization'] for request in self.requests]
        self.assertEqual(headers[0], headers[1])
        self.assertNotEqual(headers[1], headers[2])
        with patch('utils.apns.time.time', return_value=1799999999):
            with self.assertRaises(APNsUnavailable):
                self.send(transport)
        self.assertEqual(len(self.requests), 3)

    def test_unconfigured_environment_path_injection_and_invalid_payload_never_send(self):
        transport = self.transport()
        for changes in ({'environment': 'production'}, {'environment': []}, {'token': '../token'},
                        {'token': ' ab'}, {'token': 'a'}, {'token': 'ab' * 513},
                        {'notification_id': str(self.notification)}, {'payload': []}, {'payload': {'aps': {}}},
                        {'payload': {'aps': {'alert': 'x' * 4096}}}, {'payload': {'aps': {'alert': float('nan')}}}):
            with self.assertRaises(ValueError):
                self.send(transport, **changes)
        self.assertEqual(self.requests, [])

    def test_payload_limit_is_measured_in_utf8_bytes(self):
        transport = self.transport()
        with self.assertRaises(ValueError):
            self.send(transport, payload={'aps': {'alert': '\u4f60' * 1400}})
        self.assertEqual(self.requests, [])

    def test_production_is_a_separate_explicit_host(self):
        transport = self.transport(replace(self.config, environments=('production',)))
        self.send(transport, environment='production')
        self.assertEqual(self.requests[0].url.host, 'api.push.apple.com')

    def test_timeout_is_unknown_and_not_retried_or_leaked(self):
        calls = []
        def timeout(request):
            calls.append(request)
            raise httpx.ReadTimeout('secret response including token', request=request)
        result = self.send(self.transport(handler=timeout))
        self.assertEqual(result, APNsResult('unknown'))
        self.assertEqual(len(calls), 1)
        self.assertNotIn('secret', repr(result))

    def test_redirect_http1_and_mismatched_request_ids_are_not_accepted(self):
        transport = self.transport()
        for response in (httpx.Response(200), httpx.Response(302, headers={'location': 'https://evil.invalid'},
                                                            extensions={'http_version': b'HTTP/2'}),
                         httpx.Response(200, headers={'apns-id': str(uuid.uuid4())},
                                        extensions={'http_version': b'HTTP/2'})):
            self.response = response
            self.assertNotEqual(self.send(transport).outcome, 'accepted')
        self.assertEqual(len(self.requests), 3)

    def test_provider_rejection_is_redacted_and_only_typed_410_marks_invalid_token(self):
        transport = self.transport()
        for status, body, timestamp in ((410, {'reason': 'Unregistered', 'timestamp': 1800000000000}, 1800000000000),
                                        (410, {'reason': 'ExpiredToken', 'timestamp': 1800000000000}, 1800000000000),
                                        (410, {'reason': 'Unregistered', 'timestamp': True}, None),
                                        (410, {'reason': 'Unregistered', 'timestamp': '1800000000000'}, None),
                                        (400, {'reason': 'BadDeviceToken'}, None),
                                        (403, {'reason': self.pem}, None), (429, [], None), (503, {}, None)):
            self.response = httpx.Response(status, json=body, extensions={'http_version': b'HTTP/2'})
            result = self.send(transport)
            self.assertEqual(result, APNsResult('rejected', timestamp))
            self.assertNotIn(self.pem, repr(result))

    def test_malformed_or_oversized_error_response_is_bounded(self):
        transport = self.transport()
        for body, outcome in ((b'not json', 'rejected'), (b'x' * 8193, 'unknown')):
            self.response = httpx.Response(410, content=body, extensions={'http_version': b'HTTP/2'})
            self.assertEqual(self.send(transport), APNsResult(outcome))

    def test_httpx_request_logging_redacts_device_token(self):
        transport = self.transport()
        with self.assertLogs('httpx', level='INFO') as logs:
            self.send(transport)
        self.assertIn('[redacted]', '\n'.join(logs.output))
        self.assertNotIn('ab' * 32, '\n'.join(logs.output))
        with self.assertLogs('httpcore.http2', level='DEBUG') as logs:
            logging.getLogger('httpcore.http2').debug('header bearer sensitive.jwt.signature')
        self.assertNotIn('sensitive', '\n'.join(logs.output))
