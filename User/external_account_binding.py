"""Phone-verified OAuth bindings for external resource providers."""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlencode
from pathlib import Path

import requests
from cryptography.fernet import Fernet, InvalidToken
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.utils.crypto import salted_hmac
from django.views import View
from User.models import User
from User.oauth import verified_mainland_phone
from utils import auth


ORIGINS = {'https://frienden.jyonn.space', 'https://frienden.6-79.cn',
           'https://sermo.jyonn.space', 'https://sermo.6-79.cn'}
RETURN_PATH = re.compile(r'/(?:[a-zA-Z0-9_-]+/)?app/(?:menu|chats)(?:/[0-9]+)?/?')

class ExternalBindingError(Exception):
    def __init__(self, code, status=409):
        self.code = code
        self.status = status


def result(body, status=200):
    response = JsonResponse(body, status=status)
    response['Cache-Control'] = 'no-store'
    return response


def payload(request):
    try:
        value = json.loads(request.body)
        if isinstance(value, dict):
            return value
    except (ValueError, UnicodeDecodeError):
        pass
    raise ExternalBindingError('invalid_request', 400)


class ExternalAccountBinding:
    """Share encrypted provider credentials across accounts with the same verified phone."""

    def __init__(self, name, api, scopes, connections, credentials):
        self.name = name
        self.env_prefix = name.upper()
        self.api = api
        self.callback = f'https://api.sermo.jyonn.space/users/{name}/callback'
        self.scopes = scopes
        self.connections = connections
        self.credentials = credentials

    def stored_config(self):
        path = Path(os.environ.get(self.env_prefix + '_CONFIG', Path.home() / '.config/frienden' / (self.name + '.json')))
        return json.loads(path.read_text()) if path.is_file() and not path.stat().st_mode & 0o077 else {}


    def config(self):
        try:
            saved = self.stored_config()
            client = os.environ.get(self.env_prefix + '_CLIENT_ID', saved.get('client_id'))
            secret = os.environ.get(self.env_prefix + '_CLIENT_SECRET', saved.get('client_secret'))
            box = Fernet(os.environ.get(self.env_prefix + '_STORAGE_KEY', saved.get('storage_key', '')).encode())
            if client and secret:
                return client, secret, box
        except (OSError, ValueError, AttributeError):
            pass
        raise ExternalBindingError('unavailable', 503)


    def seal(self, value):
        return self.config()[2].encrypt(json.dumps(value).encode()).decode()


    def open_sealed(self, value, ttl=None):
        try:
            return json.loads(self.config()[2].decrypt(value.encode(), ttl=ttl))
        except (InvalidToken, ValueError):
            raise ExternalBindingError('connect_required') from None


    def phone_key(self, phone):
        return salted_hmac(self.name + '-verified-phone', phone, algorithm='sha256').hexdigest()

    def remote(self, path, *, data=None, access=None, params=None, idempotency_key=None):
        client, secret, _ = self.config()
        headers = {'Authorization': f'Bearer {access}'} if access else {}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        try:
            response = requests.request(
                'POST' if data is not None else 'GET', self.api + path, json=data, params=params,
                headers=headers,
                auth=None if access else (client, secret), timeout=(3, 8), allow_redirects=False,
            )
            value = response.json()
        except (requests.RequestException, ValueError):
            raise ExternalBindingError('unavailable', 503) from None
        if response.status_code not in (200, 201):
            code = value.get('error') if isinstance(value, dict) else None
            if code == 'account_mismatch':
                raise ExternalBindingError('account_mismatch')
            if code in ('invalid_grant', 'invalid_token', 'insufficient_scope'):
                raise ExternalBindingError('connect_required')
            if code in ('resource_not_found', 'album_not_found'):
                raise ExternalBindingError('resource_not_found', 404)
            raise ExternalBindingError('unavailable', 503)
        if not isinstance(value, dict):
            raise ExternalBindingError('unavailable', 503)
        return value


    def credential(self, value):
        access, refresh, expires = (value.get(key) for key in ('access_token', 'refresh_token', 'expires_in'))
        if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh
                and type(expires) is int and 0 < expires <= 3600
                and set(self.scopes.split()) <= set(str(value.get('scope', '')).split())):
            raise ExternalBindingError('unavailable', 503)
        return {'access': access, 'refresh': refresh, 'expires': time.time() + expires, 'can_share': True}


    def prove_identity(self, access, phone):
        value = self.remote('frienden-account/', data={'token': access, 'phone': phone})
        subject = value.get('sub', '')
        if not isinstance(subject, str) or not re.fullmatch(re.escape(self.name) + r':[0-9a-f-]{36}', subject):
            raise ExternalBindingError('unavailable', 503)
        return subject


    def status(self, user):
        phone = verified_mainland_phone(user)
        if not phone:
            return 'phone_required'
        row = self.connections.objects.filter(user=user, phone_digest=self.phone_key(phone)).select_related('credential').first()
        if row and row.credential and row.credential.sealed and self.open_sealed(row.credential.sealed).get('can_share'):
            return 'connected'
        return 'connect_required'


    def start(self, user, origin, path):
        client, _, _ = self.config()
        if origin not in ORIGINS or not isinstance(path, str) or not RETURN_PATH.fullmatch(path):
            raise ExternalBindingError('invalid_request', 400)
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk)
            phone = verified_mainland_phone(user)
            if not phone:
                raise ExternalBindingError('phone_required')
            state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
            row, _ = self.connections.objects.get_or_create(user=user)
            row.pending = self.seal({'state': state, 'verifier': verifier, 'phone_key': self.phone_key(phone),
                                'origin': origin, 'path': path})
            row.save(update_fields=['pending'])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return self.api + 'authorize/?' + urlencode({
            'response_type': 'code', 'client_id': client, 'redirect_uri': self.callback, 'scope': self.scopes,
            'state': f'{user.pk}.{state}', 'code_challenge': challenge, 'code_challenge_method': 'S256',
        })


    def consume(self, state):
        match = re.fullmatch(r'([1-9][0-9]{0,18})\.([a-zA-Z0-9_-]{43})', state)
        if not match:
            raise ExternalBindingError('connect_required')
        with transaction.atomic():
            user = User.objects.select_for_update().filter(pk=int(match[1])).first()
            row = self.connections.objects.filter(user=user).first() if user else None
            if not row or not row.pending or row.pending.startswith('claimed:'):
                raise ExternalBindingError('connect_required')
            pending = self.open_sealed(row.pending, ttl=600)
            if not secrets.compare_digest(pending['state'], match[2]):
                raise ExternalBindingError('connect_required')
            operation = 'claimed:' + match[2]
            row.pending = operation
            row.save(update_fields=['pending'])
            return row.pk, operation, pending


    def finish(self, row_id, operation, pending, code):
        if not isinstance(code, str) or not 0 < len(code) <= 512:
            raise ExternalBindingError('denied')
        with transaction.atomic():
            user_id = self.connections.objects.filter(pk=row_id).values_list('user_id', flat=True).first()
            user = User.objects.select_for_update().filter(pk=user_id).first()
            row = self.connections.objects.filter(pk=row_id).first()
            phone = verified_mainland_phone(user) if user else None
            if not row or row.pending != operation or not phone or pending['phone_key'] != self.phone_key(phone):
                raise ExternalBindingError('connect_required')
            shared, _ = self.credentials.objects.select_for_update().get_or_create(phone_digest=pending['phone_key'])
            tokens = self.credential(self.remote('token/', data={'grant_type': 'authorization_code', 'code': code,
                                                       'redirect_uri': self.callback, 'code_verifier': pending['verifier']}))
            tokens['subject'] = self.prove_identity(tokens['access'], phone)
            if shared.sealed and self.open_sealed(shared.sealed).get('subject') != tokens['subject']:
                raise ExternalBindingError('account_mismatch')
            shared.sealed = self.seal(tokens)
            shared.save(update_fields=['sealed'])
            row.credential = shared
            row.phone_digest = pending['phone_key']
            row.pending = ''
            row.save(update_fields=['credential', 'phone_digest', 'pending'])


    def access_for(self, user):
        phone = verified_mainland_phone(user)
        if not phone:
            raise ExternalBindingError('phone_required')
        key = self.phone_key(phone)
        row = self.connections.objects.filter(user=user, phone_digest=key, credential_id=key).first()
        if not row:
            raise ExternalBindingError('connect_required')
        failure = None
        with transaction.atomic():
            shared = self.credentials.objects.select_for_update().get(pk=key)
            if not shared.sealed:
                raise ExternalBindingError('connect_required')
            tokens = self.open_sealed(shared.sealed)
            if not tokens.get('can_share'):
                raise ExternalBindingError('connect_required')
            try:
                if tokens['expires'] <= time.time() + 20:
                    updated = self.credential(self.remote('token/', data={'grant_type': 'refresh_token',
                                                                'refresh_token': tokens['refresh']}))
                    updated['subject'] = tokens['subject']
                    tokens = updated
                    shared.sealed = self.seal(tokens)
                    shared.save(update_fields=['sealed'])
                if self.prove_identity(tokens['access'], phone) != tokens['subject']:
                    raise ExternalBindingError('account_mismatch')
            except ExternalBindingError as error:
                # If rotation reached the provider, replaying the old refresh token can revoke the grant.
                if error.code != 'unavailable' or tokens['expires'] <= time.time() + 20:
                    shared.sealed = ''
                    shared.save(update_fields=['sealed'])
                failure = error
        if failure:
            raise failure
        return tokens['access']


class ExternalCallbackView(View):
    binding = None

    def get(self, request):
        try:
            row_id, operation, pending = self.binding.consume(request.GET.get('state', ''))
        except ExternalBindingError as error:
            return result({'error': error.code}, error.status)
        try:
            self.binding.finish(row_id, operation, pending, request.GET.get('code', ''))
            outcome = 'connected'
        except ExternalBindingError as error:
            outcome = error.code
        response = HttpResponseRedirect(pending['origin'] + pending['path'] + '?' + urlencode({self.binding.name: outcome}))
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class ExternalConnectionView(View):
    binding = None

    @auth.require_user
    def get(self, request):
        try:
            self.binding.config()
            return result({'status': self.binding.status(request.user)})
        except ExternalBindingError as error:
            return result({'status': error.code})

    @auth.require_user
    def post(self, request):
        try:
            data = payload(request)
            if data.get('action') == 'disconnect':
                with transaction.atomic():
                    User.objects.select_for_update().get(pk=request.user.pk)
                    self.binding.connections.objects.filter(user=request.user).update(
                        credential=None, phone_digest='', pending='')
                return result({'status': 'connect_required'})
            if data.get('action') != 'connect':
                raise ExternalBindingError('invalid_request', 400)
            return result({'authorize_url': self.binding.start(request.user, data.get('origin'), data.get('path'))})
        except ExternalBindingError as error:
            return result({'error': error.code}, error.status)
