"""Confidential, fixed-route Chronelle binding client. No credentials reach JS."""
import base64
import hashlib
import json
import os
import secrets
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet, InvalidToken
import requests
from django.db import transaction
from django.utils import timezone
from django.views import View
from User.models import ChronelleAccount
from User.chronelle_views import authorization_site, APP_ORIGINS, BASE, MIRROR_BASE
from Chat.research import fail
from utils import auth


class BindingFailure(Exception):
    def __init__(self, code, status=409):
        self.code, self.status = code, status


def config():
    saved = {}
    path = Path(os.environ.get('CHRONELLE_BINDING_CONFIG',str(Path.home()/'.config/frienden/chronelle-binding.json')))
    if path.is_file():
        if path.stat().st_mode & 0o077:
            raise BindingFailure('integration_not_configured',503)
        try:
            saved = json.loads(path.read_text())
        except (OSError,ValueError):
            raise BindingFailure('integration_not_configured',503)
    client = os.environ.get('CHRONELLE_BINDING_CLIENT_ID', saved.get('client_id',''))
    secret = os.environ.get('CHRONELLE_BINDING_CLIENT_SECRET', saved.get('client_secret',''))
    key = os.environ.get('CHRONELLE_BINDING_STORAGE_KEY', saved.get('storage_key',''))
    if not all((client, secret, key)):
        raise BindingFailure('integration_not_configured', 503)
    try:
        UUID(client)
        return client, secret, Fernet(key.encode())
    except (ValueError, TypeError, AttributeError):
        raise BindingFailure('integration_not_configured', 503)


def unpack(row):
    if not row.sealed:
        return {}
    try:
        return json.loads(config()[2].decrypt(row.sealed.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise BindingFailure('binding_required')


def store(row, value):
    row.sealed = config()[2].encrypt(json.dumps(value).encode()).decode()
    row.save(update_fields=['sealed'])


def remote(base, path, *, method='POST', body=None, token=None, key=None):
    if base not in (BASE, MIRROR_BASE) or not path.startswith('/api/account-bindings/v1/'):
        raise BindingFailure('integration_not_configured', 503)
    client, secret, _ = config()
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if key:
        headers['Idempotency-Key'] = key
    try:
        response = requests.request(method, base + path, json=body, headers=headers,
            auth=None if token else (client, secret), timeout=15, allow_redirects=False)
        value = response.json() if response.content else {}
    except (requests.RequestException, ValueError):
        raise BindingFailure('chronelle_unavailable', 503)
    if not 200 <= response.status_code < 300:
        code = value.get('code', 'chronelle_unavailable') if isinstance(value, dict) else 'chronelle_unavailable'
        # Only protocol codes, never upstream bodies, tokens or translated prose.
        raise BindingFailure(code if isinstance(code, str) and code.replace('_', '').isalnum() else 'chronelle_unavailable', response.status_code if response.status_code in (400,401,403,409,410,429) else 503)
    return value


def credentials(user, base):
    """Serialize rotating refresh. A lost response is not retried with the old pair."""
    renewal = None
    with transaction.atomic():
        row, _ = ChronelleAccount.objects.get_or_create(user=user)
        row = ChronelleAccount.objects.select_for_update().get(pk=row.pk)
        value = unpack(row)
        if not value.get('binding') or value.get('renewal_uncertain'):
            raise BindingFailure('binding_required')
        if value.get('access_until', 0) <= timezone.now().timestamp() + 30:
            # Commit uncertainty before the one-shot request, including process crashes.
            value['renewal_uncertain'] = True
            store(row, value)
            renewal = value['refresh_token']
    if renewal:
        try:
            fresh = remote(base, '/api/account-bindings/v1/token/', body={'grant_type':'refresh_token','refresh_token':renewal})
            with transaction.atomic():
                row = ChronelleAccount.objects.select_for_update().get(user=user)
                current = unpack(row)
                if current.get('refresh_token') != renewal:
                    raise BindingFailure('binding_required')
                value.update(access_token=fresh['access_token'], refresh_token=fresh['refresh_token'],
                    access_until=timezone.now().timestamp()+fresh['expires_in'], renewal_uncertain=False)
                store(row,value)
        except (BindingFailure, KeyError, TypeError):
            raise BindingFailure('binding_required')
    return value


def claim(user, origin):
    with transaction.atomic():
        row, _ = ChronelleAccount.objects.get_or_create(user=user)
        row = ChronelleAccount.objects.select_for_update().get(pk=row.pk)
        value = unpack(row)
        pending = value.get('pending', {})
        if pending.get('until',0) <= timezone.now().timestamp() or pending.get('origin') != origin:
            return {'status':'expired'}
        if pending.get('after',0) > timezone.now().timestamp():
            return {'status':'pending'}
        value.pop('pending',None)
        value['claim_id'] = pending['request_id']
        store(row,value)
    result = remote(pending['base'], '/api/account-bindings/v1/requests/'+pending['request_id']+'/code/', body={'code_verifier':pending['verifier']})
    if result.get('status') == 'pending':
        with transaction.atomic():
            row = ChronelleAccount.objects.select_for_update().get(user=user)
            value = unpack(row)
            if value.get('claim_id') != pending['request_id']:
                raise BindingFailure('binding_required')
            pending['after'] = timezone.now().timestamp()+max(3,result.get('retry_after',3))
            value['pending'] = pending;store(row,value)
        return {'status':'pending'}
    if not secrets.compare_digest(result.get('state',''),pending['state']):
        raise BindingFailure('invalid_state',403)
    fresh = remote(pending['base'], '/api/account-bindings/v1/token/', body={'grant_type':'authorization_code','request_id':pending['request_id'],'code':result['authorization_code'],'code_verifier':pending['verifier'],'redirect_uri':pending['redirect']})
    binding = fresh['binding']
    if binding.get('binding_version') != 1 or binding.get('client_id') != config()[0]:
        raise BindingFailure('invalid_binding',403)
    UUID(binding['subject']);UUID(binding['binding_id'])
    with transaction.atomic():
        row = ChronelleAccount.objects.select_for_update().get(user=user)
        if unpack(row).get('claim_id') != pending['request_id']:
            raise BindingFailure('binding_required')
        store(row,{'binding':binding,'access_token':fresh['access_token'],'refresh_token':fresh['refresh_token'],'access_until':timezone.now().timestamp()+fresh['expires_in']})
    return {'status':'active'}


def calendar(user, base, resource, *, method='GET', body=None, key=None):
    if resource not in ('groups/', 'identity/', 'display-sessions/', 'invitations/') and not resource.startswith('display-session/'):
        raise BindingFailure('calendar_permission_denied', 403)
    value = credentials(user, base)
    return remote(base, '/api/account-bindings/v1/calendar/'+resource, method=method, body=body, token=value['access_token'], key=key)


def revocation_credentials(user,base):
    try:
        return credentials(user,base)
    except BindingFailure:
        row=ChronelleAccount.objects.filter(user=user).first()
        value=unpack(row) if row else {}
        if value.get('revoked'):
            return {'revoked':True}
        if value.get('binding'):
            status=remote(base,'/api/account-bindings/v1/introspect/',body={'binding_id':value['binding']['binding_id']})
            if status.get('active') is False:return {'revoked':True}
        raise


class FullChronelleView(View):
    @auth.require_user
    def get(self, request):
        try:
            base, _ = authorization_site(request)
            config()
            row = ChronelleAccount.objects.filter(user=request.user).first()
            value = unpack(row) if row else {}
            if not value.get('binding') or value.get('renewal_uncertain'):
                return {'status':'binding_required'}
            result = remote(base, '/api/account-bindings/v1/introspect/', body={'binding_id':value['binding']['binding_id']})
            return {'status':'active' if result.get('active') else 'binding_required'}
        except BindingFailure as error:
            return {'status':error.code}

    @auth.require_user
    def post(self, request):
        try:
            if len(request.body)>8192:
                raise BindingFailure('invalid_request',413)
            body = json.loads(request.body)
            if not isinstance(body,dict):
                raise BindingFailure('invalid_request',400)
            base, origin = authorization_site(request)
            if request.headers.get('Origin') not in APP_ORIGINS:
                raise BindingFailure('origin_not_allowed', 403)
            config()
            if body.get('action') == 'claim':
                return claim(request.user,origin)
            with transaction.atomic():
                row, _ = ChronelleAccount.objects.get_or_create(user=request.user)
                row = ChronelleAccount.objects.select_for_update().get(pk=row.pk)
                try:
                    value = unpack(row)
                except BindingFailure:
                    if body.get('action')!='authorize' or body.get('confirmed') is not True:
                        raise
                    # Lost encryption material cannot recover old authority. Only
                    # a fresh user-confirmed binding may replace that ciphertext.
                    value = {}
                if body.get('action') == 'authorize':
                    if body.get('confirmed') is not True:
                        raise BindingFailure('confirmation_required', 400)
                    pending = value.get('pending', {})
                    if pending.get('until', 0) > timezone.now().timestamp() and pending.get('origin') == origin:
                        return {'authorize_url':pending['url']}
                    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                    redirect = origin+'/app/research'
                    result = remote(base, '/api/account-bindings/v1/requests/', body={'state':state,'code_challenge':challenge,'code_challenge_method':'S256','redirect_uri':redirect,'binding_version':1})
                    uid = str(UUID(result['request_id']))
                    url = base+'/#/bind-calendar/'+uid
                    value.pop('claim_id',None)
                    value['pending'] = {'request_id':uid,'verifier':verifier,'state':state,'redirect':redirect,'origin':origin,'base':base,'url':url,'until':timezone.now().timestamp()+min(result['expires_in'],600),'after':0}
                    store(row, value)
                    return {'authorize_url':url}
                if body.get('action') == 'revoke':
                    if body.get('confirmed') is not True:
                        raise BindingFailure('confirmation_required',400)
                    if value.get('binding'):
                        remote(base,'/api/account-bindings/v1/revoke/',body={'binding_id':value['binding']['binding_id'],'confirmed':True})
                    store(row,{'revoked':True})
                    return {'status':'binding_required'}
                raise BindingFailure('invalid_request',400)
        except BindingFailure as error:
            return fail(error.code,error.status)
        except (ValueError, KeyError, TypeError):
            return fail('invalid_binding_response',400)
