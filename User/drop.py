"""Drop picker: one explicit OAuth grant per Frienden account, never phone-shared."""
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit, parse_qs

import requests
from django.db import transaction
from django.views import View

from User.external_account_binding import (ExternalAccountBinding, ExternalBindingError,
    ExternalCallbackView, ORIGINS, RETURN_PATH, payload, result)
from User.models import DropConnection, User
from utils import auth

API = 'https://api.drop.jyonn.space/'
HOME = 'https://drop.jyonn.space'
FIELDS = {'text', 'occurred_at', 'audio', 'city', 'place', 'latitude', 'longitude'}
IDENTIFIER = re.compile(r'[A-Za-z0-9._~-]{1,128}')
UUID = re.compile(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')


def share_url(value):
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        query = parse_qs(url.query, keep_blank_values=True)
        token = query.get('share', [])
        if (url.scheme != 'https' or url.netloc != 'drop.jyonn.space' or url.path != '/'
                or url.fragment or set(query) != {'share'} or len(token) != 1
                or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token[0])):
            return None
        return HOME + '/?share=' + token[0]
    except ValueError:
        return None


class DropBinding(ExternalAccountBinding):
    def __init__(self):
        super().__init__('drop', API, 'diary.picker shares.create', DropConnection, None)

    def remote(self, path, *, data=None, access=None, params=None, idempotency_key=None):
        client, secret, _ = self.config()
        headers = {'Authorization': f'Bearer {access}'} if access else {}
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        try:
            response = requests.request('POST' if data is not None else 'GET', API + path,
                json=data, params=params, headers=headers, auth=None if access else (client, secret),
                timeout=(3, 15 if path == 'api/oauth/shares/' else 8), allow_redirects=False)
            value = response.json()
        except (requests.RequestException, ValueError):
            raise ExternalBindingError('unavailable', 503) from None
        if not isinstance(value, dict):
            raise ExternalBindingError('unavailable', 503)
        if response.status_code not in (200, 201):
            code = value.get('error')
            if code in ('invalid_token', 'invalid_grant', 'insufficient_scope'):
                raise ExternalBindingError('connect_required', 409)
            if code in ('invalid_selection', 'invalid_request', 'insufficient_fields', 'idempotency_conflict', 'share_gone'):
                raise ExternalBindingError(code, response.status_code)
            raise ExternalBindingError('unavailable', 503)
        return value

    def status(self, user):
        row = DropConnection.objects.filter(user=user).first()
        if row and row.sealed:
            try:
                if self.open_sealed(row.sealed).get('can_share'):
                    return 'connected'
            except ExternalBindingError:
                pass
        return 'connect_required'

    def start(self, user, origin, path):
        client, _, _ = self.config()
        if origin not in ORIGINS or not isinstance(path, str) or not RETURN_PATH.fullmatch(path):
            raise ExternalBindingError('invalid_request', 400)
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        with transaction.atomic():
            User.objects.select_for_update().get(pk=user.pk)
            row, _ = DropConnection.objects.get_or_create(user=user)
            row.pending = self.seal({'state': state, 'verifier': verifier, 'origin': origin, 'path': path})
            row.save(update_fields=['pending'])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        return API + 'oauth/authorize/?' + urlencode(dict(response_type='code', client_id=client,
            redirect_uri=self.callback, scope=self.scopes, state=f'{user.pk}.{state}',
            code_challenge=challenge, code_challenge_method='S256'))

    def finish(self, row_id, operation, pending, code):
        if not isinstance(code, str) or not 0 < len(code) <= 512:
            raise ExternalBindingError('denied')
        with transaction.atomic():
            row = DropConnection.objects.select_for_update().filter(pk=row_id).first()
            if not row or row.pending != operation:
                raise ExternalBindingError('connect_required')
            value = self.remote('oauth/token/', data=dict(grant_type='authorization_code', code=code,
                redirect_uri=self.callback, code_verifier=pending['verifier']))
            row.sealed = self.seal(self.credential(value)); row.pending = ''
            row.save(update_fields=['sealed', 'pending'])

    def access_for(self, user):
        failure = None
        with transaction.atomic():
            row = DropConnection.objects.select_for_update().filter(user=user).first()
            if not row or not row.sealed:
                raise ExternalBindingError('connect_required')
            tokens = self.open_sealed(row.sealed)
            if tokens['expires'] <= time.time() + 20:
                try:
                    tokens = self.credential(self.remote('oauth/token/', data={
                        'grant_type': 'refresh_token', 'refresh_token': tokens['refresh']}))
                    row.sealed = self.seal(tokens)
                except ExternalBindingError as error:
                    # A timed-out rotation may have succeeded upstream; never replay its old token.
                    row.sealed = ''; failure = error
                row.save(update_fields=['sealed'])
        if failure:
            raise failure
        return tokens['access']

    def invalidate(self, user, access):
        if not access:
            return
        with transaction.atomic():
            row = DropConnection.objects.select_for_update().filter(user=user).first()
            if row and row.sealed and self.open_sealed(row.sealed).get('access') == access:
                row.sealed = ''; row.save(update_fields=['sealed'])


binding = DropBinding()


class DropCallbackView(ExternalCallbackView):
    binding = binding

    def get(self, request):
        if any(len(request.GET.getlist(key)) > 1 for key in ('state', 'code', 'error')):
            return result({'error': 'invalid_request'}, 400)
        return super().get(request)


class DropConnectionView(View):
    @auth.require_user
    def get(self, request):
        try:
            client, _, _ = binding.config()
            return result({'status': binding.status(request.user), 'client_id': client})
        except ExternalBindingError as error:
            return result({'status': error.code})

    @auth.require_user
    def post(self, request):
        try:
            data = payload(request)
            if data.get('action') == 'connect':
                return result({'authorize_url': binding.start(request.user, data.get('origin'), data.get('path'))})
            if data.get('action') != 'disconnect':
                raise ExternalBindingError('invalid_request', 400)
            with transaction.atomic():
                User.objects.select_for_update().get(pk=request.user.pk)
                row = DropConnection.objects.select_for_update().filter(user=request.user).first()
                if row:
                    if row.sealed:
                        try:
                            tokens = binding.open_sealed(row.sealed)
                            binding.remote('oauth/revoke/', data={'token': tokens['refresh']})
                        except ExternalBindingError:
                            # Keep the local grant so a failed revocation can be retried explicitly.
                            raise ExternalBindingError('unavailable', 503) from None
                    row.sealed = ''; row.pending = ''; row.save(update_fields=['sealed', 'pending'])
            return result({'status': 'connect_required'})
        except ExternalBindingError as error:
            return result({'error': error.code}, error.status)


class DropEntriesView(View):
    @auth.require_user
    def get(self, request):
        access = None
        try:
            query = request.GET.get('q', '')
            offset = request.GET.get('offset', '0')
            if len(query) > 200 or not re.fullmatch(r'\d{1,6}', offset) or int(offset) > 100000:
                raise ExternalBindingError('invalid_request', 400)
            access = binding.access_for(request.user)
            value = binding.remote('api/oauth/picker/entries/', access=access,
                                   params={'q': query, 'offset': int(offset)})
            if not isinstance(value.get('entries'), list) or not isinstance(value.get('allowed_fields'), list):
                raise ExternalBindingError('unavailable', 503)
            return result(value)
        except ExternalBindingError as error:
            if error.code == 'connect_required': binding.invalidate(request.user, access)
            return result({'error': error.code}, error.status)


class DropShareView(View):
    @auth.require_user
    def post(self, request):
        access = None
        try:
            data = payload(request)
            ids, fields, expires, operation = (data.get(key) for key in ('entry_ids', 'fields', 'expires_in', 'operation_id'))
            if (not isinstance(ids, list) or not 1 <= len(ids) <= 100
                    or any(not isinstance(item, str) or not UUID.fullmatch(item) for item in ids)
                    or len(set(ids)) != len(ids) or not isinstance(fields, list)
                    or any(not isinstance(item, str) for item in fields)
                    or not {'text', 'occurred_at'} <= set(fields) <= FIELDS
                    or len(fields) != len(set(fields)) or type(expires) is not int or not 60 <= expires <= 2592000
                    or not isinstance(operation, str) or not IDENTIFIER.fullmatch(operation)):
                raise ExternalBindingError('invalid_request', 400)
            key = hashlib.sha256(f'{request.user.pk}:{operation}'.encode()).hexdigest()
            access = binding.access_for(request.user)
            value = binding.remote('api/oauth/shares/', data={'entry_ids': ids, 'fields': fields, 'expires_in': expires},
                                   access=access, idempotency_key=key)
            url = share_url(value.get('url'))
            if not url:
                raise ExternalBindingError('unavailable', 503)
            return result({key: value.get(key) for key in ('id', 'fields', 'expires_at')} | {'url': url})
        except ExternalBindingError as error:
            if error.code == 'connect_required': binding.invalidate(request.user, access)
            return result({'error': error.code}, error.status)
