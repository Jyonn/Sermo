"""Connect the current Frienden space account to its verified Pocklo account."""
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

from User.models import PockloConnection, PockloCredential, User
from User.oauth import verified_mainland_phone
from Message.providers import pocklo as public_shares
from utils import auth


API = 'https://disk.6-79.cn/api/oauth/'
CALLBACK = 'https://api.sermo.jyonn.space/users/pocklo/callback'
ORIGINS = {'https://frienden.jyonn.space', 'https://frienden.6-79.cn',
           'https://sermo.jyonn.space', 'https://sermo.6-79.cn'}
RETURN_PATH = re.compile(r'/(?:[a-zA-Z0-9_-]+/)?app/(?:menu|chats)(?:/[0-9]+)?/?')
RESOURCE_ID = re.compile(r'[a-zA-Z0-9_-]{1,128}')
SHARE_SCOPES = 'files.read shares.create'


class PockloError(Exception):
    def __init__(self, code, status=409):
        self.code = code
        self.status = status


def stored_config():
    path = Path(os.environ.get('POCKLO_CONFIG', Path.home() / '.config/frienden/pocklo.json'))
    return json.loads(path.read_text()) if path.is_file() and not path.stat().st_mode & 0o077 else {}


def config():
    try:
        saved = stored_config()
        client = os.environ.get('POCKLO_CLIENT_ID', saved.get('client_id'))
        secret = os.environ.get('POCKLO_CLIENT_SECRET', saved.get('client_secret'))
        box = Fernet(os.environ.get('POCKLO_STORAGE_KEY', saved.get('storage_key', '')).encode())
        if client and secret:
            return client, secret, box
    except (OSError, ValueError, AttributeError):
        pass
    raise PockloError('unavailable', 503)


def seal(value):
    return config()[2].encrypt(json.dumps(value).encode()).decode()


def open_sealed(value, ttl=None):
    try:
        return json.loads(config()[2].decrypt(value.encode(), ttl=ttl))
    except (InvalidToken, ValueError):
        raise PockloError('connect_required') from None


def phone_key(phone):
    return salted_hmac('pocklo-verified-phone', phone, algorithm='sha256').hexdigest()


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
    raise PockloError('invalid_request', 400)


def remote(path, *, data=None, access=None, params=None, idempotency_key=None):
    client, secret, _ = config()
    headers = {'Authorization': f'Bearer {access}'} if access else {}
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key
    try:
        response = requests.request(
            'POST' if data is not None else 'GET', API + path, json=data, params=params,
            headers=headers,
            auth=None if access else (client, secret), timeout=(3, 8), allow_redirects=False,
        )
        value = response.json()
    except (requests.RequestException, ValueError):
        raise PockloError('unavailable', 503) from None
    if response.status_code not in (200, 201):
        code = value.get('error') if isinstance(value, dict) else None
        if code == 'account_mismatch':
            raise PockloError('account_mismatch')
        if code in ('invalid_grant', 'invalid_token', 'insufficient_scope'):
            raise PockloError('connect_required')
        if code == 'resource_not_found':
            raise PockloError('resource_not_found', 404)
        raise PockloError('unavailable', 503)
    if not isinstance(value, dict):
        raise PockloError('unavailable', 503)
    return value


def credential(value):
    access, refresh, expires = (value.get(key) for key in ('access_token', 'refresh_token', 'expires_in'))
    if not (isinstance(access, str) and access and isinstance(refresh, str) and refresh
            and type(expires) is int and 0 < expires <= 3600
            and set(SHARE_SCOPES.split()) <= set(str(value.get('scope', '')).split())):
        raise PockloError('unavailable', 503)
    return {'access': access, 'refresh': refresh, 'expires': time.time() + expires, 'can_share': True}


def prove_identity(access, phone):
    value = remote('frienden-account/', data={'token': access, 'phone': phone})
    subject = value.get('sub', '')
    if not isinstance(subject, str) or not re.fullmatch(r'pocklo:[0-9a-f-]{36}', subject):
        raise PockloError('unavailable', 503)
    return subject


def status(user):
    phone = verified_mainland_phone(user)
    if not phone:
        return 'phone_required'
    row = PockloConnection.objects.filter(user=user, phone_digest=phone_key(phone)).select_related('credential').first()
    if row and row.credential and row.credential.sealed and open_sealed(row.credential.sealed).get('can_share'):
        return 'connected'
    return 'connect_required'


def start(user, origin, path):
    client, _, _ = config()
    if origin not in ORIGINS or not isinstance(path, str) or not RETURN_PATH.fullmatch(path):
        raise PockloError('invalid_request', 400)
    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=user.pk)
        phone = verified_mainland_phone(user)
        if not phone:
            raise PockloError('phone_required')
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        row, _ = PockloConnection.objects.get_or_create(user=user)
        row.pending = seal({'state': state, 'verifier': verifier, 'phone_key': phone_key(phone),
                            'origin': origin, 'path': path})
        row.save(update_fields=['pending'])
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return API + 'authorize/?' + urlencode({
        'response_type': 'code', 'client_id': client, 'redirect_uri': CALLBACK, 'scope': SHARE_SCOPES,
        'state': f'{user.pk}.{state}', 'code_challenge': challenge, 'code_challenge_method': 'S256',
    })


def consume(state):
    match = re.fullmatch(r'([1-9][0-9]{0,18})\.([a-zA-Z0-9_-]{43})', state)
    if not match:
        raise PockloError('connect_required')
    with transaction.atomic():
        user = User.objects.select_for_update().filter(pk=int(match[1])).first()
        row = PockloConnection.objects.filter(user=user).first() if user else None
        if not row or not row.pending or row.pending.startswith('claimed:'):
            raise PockloError('connect_required')
        pending = open_sealed(row.pending, ttl=600)
        if not secrets.compare_digest(pending['state'], match[2]):
            raise PockloError('connect_required')
        operation = 'claimed:' + match[2]
        row.pending = operation
        row.save(update_fields=['pending'])
        return row.pk, operation, pending


def finish(row_id, operation, pending, code):
    if not isinstance(code, str) or not 0 < len(code) <= 512:
        raise PockloError('denied')
    with transaction.atomic():
        user_id = PockloConnection.objects.filter(pk=row_id).values_list('user_id', flat=True).first()
        user = User.objects.select_for_update().filter(pk=user_id).first()
        row = PockloConnection.objects.filter(pk=row_id).first()
        phone = verified_mainland_phone(user) if user else None
        if not row or row.pending != operation or not phone or pending['phone_key'] != phone_key(phone):
            raise PockloError('connect_required')
        shared, _ = PockloCredential.objects.select_for_update().get_or_create(phone_digest=pending['phone_key'])
        tokens = credential(remote('token/', data={'grant_type': 'authorization_code', 'code': code,
                                                   'redirect_uri': CALLBACK, 'code_verifier': pending['verifier']}))
        tokens['subject'] = prove_identity(tokens['access'], phone)
        if shared.sealed and open_sealed(shared.sealed).get('subject') != tokens['subject']:
            raise PockloError('account_mismatch')
        shared.sealed = seal(tokens)
        shared.save(update_fields=['sealed'])
        row.credential = shared
        row.phone_digest = pending['phone_key']
        row.pending = ''
        row.save(update_fields=['credential', 'phone_digest', 'pending'])


def access_for(user):
    phone = verified_mainland_phone(user)
    if not phone:
        raise PockloError('phone_required')
    key = phone_key(phone)
    row = PockloConnection.objects.filter(user=user, phone_digest=key, credential_id=key).first()
    if not row:
        raise PockloError('connect_required')
    failure = None
    with transaction.atomic():
        shared = PockloCredential.objects.select_for_update().get(pk=key)
        if not shared.sealed:
            raise PockloError('connect_required')
        tokens = open_sealed(shared.sealed)
        if not tokens.get('can_share'):
            raise PockloError('connect_required')
        try:
            if tokens['expires'] <= time.time() + 20:
                updated = credential(remote('token/', data={'grant_type': 'refresh_token',
                                                            'refresh_token': tokens['refresh']}))
                updated['subject'] = tokens['subject']
                tokens = updated
                shared.sealed = seal(tokens)
                shared.save(update_fields=['sealed'])
            if prove_identity(tokens['access'], phone) != tokens['subject']:
                raise PockloError('account_mismatch')
        except PockloError as error:
            # If rotation reached the provider, replaying the old refresh token can revoke the grant.
            if error.code != 'unavailable' or tokens['expires'] <= time.time() + 20:
                shared.sealed = ''
                shared.save(update_fields=['sealed'])
            failure = error
    if failure:
        raise failure
    return tokens['access']


class PockloCallbackView(View):
    def get(self, request):
        try:
            row_id, operation, pending = consume(request.GET.get('state', ''))
        except PockloError as error:
            return result({'error': error.code}, error.status)
        try:
            finish(row_id, operation, pending, request.GET.get('code', ''))
            outcome = 'connected'
        except PockloError as error:
            outcome = error.code
        response = HttpResponseRedirect(pending['origin'] + pending['path'] + '?' + urlencode({'pocklo': outcome}))
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class PockloConnectionView(View):
    @auth.require_user
    def get(self, request):
        try:
            config()
            return result({'status': status(request.user)})
        except PockloError as error:
            return result({'status': error.code})

    @auth.require_user
    def post(self, request):
        try:
            data = payload(request)
            if data.get('action') == 'disconnect':
                with transaction.atomic():
                    User.objects.select_for_update().get(pk=request.user.pk)
                    PockloConnection.objects.filter(user=request.user).update(
                        credential=None, phone_digest='', pending='')
                return result({'status': 'connect_required'})
            if data.get('action') != 'connect':
                raise PockloError('invalid_request', 400)
            return result({'authorize_url': start(request.user, data.get('origin'), data.get('path'))})
        except PockloError as error:
            return result({'error': error.code}, error.status)


class PockloFilesView(View):
    @auth.require_user
    def get(self, request, resource_id=''):
        try:
            if resource_id and not RESOURCE_ID.fullmatch(resource_id):
                raise PockloError('invalid_request', 400)
            search = request.GET.get('q', '').strip()
            if len(search) > 256:
                raise PockloError('invalid_request', 400)
            access = access_for(request.user)
            path = 'files/' + (resource_id + '/' if resource_id else '')
            params = {'q': search} if search else {'offset': request.GET.get('offset', '0')}
            value = remote(path, access=access, params=params)
            return result(value)
        except PockloError as error:
            return result({'error': error.code}, error.status)


class PockloShareView(View):
    @auth.require_user
    def post(self, request):
        try:
            data = payload(request)
            item, key = data.get('resource_id'), data.get('operation_id')
            if not isinstance(item, str) or not RESOURCE_ID.fullmatch(item) or not isinstance(key, str) or not re.fullmatch(public_shares.TOKEN, key):
                raise PockloError('invalid_request', 400)
            response = remote('shares/', data={'resource_id': item}, access=access_for(request.user),
                              idempotency_key=f'{request.user.pk}:{key}')
            token = public_shares.share_token(response.get('url') or '')
            if not token:
                raise PockloError('unavailable', 503)
            return result({'url': f'https://{public_shares.HOST}/share/{token}'})
        except PockloError as error:
            return result({'error': error.code}, error.status)


class PockloShareDownloadView(View):
    resolve_target = staticmethod(public_shares.download_target)

    def get(self, request, token):
        try:
            target = self.resolve_target(str(token))
        except ValueError:
            return result({'error': 'share_unavailable'}, 404)
        except requests.RequestException:
            return result({'error': 'unavailable'}, 503)
        response = HttpResponseRedirect(target)
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class PockloShareThumbnailView(PockloShareDownloadView):
    resolve_target = staticmethod(public_shares.thumbnail_target)
