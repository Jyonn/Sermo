"""Confidential, fixed-route Chronelle binding client. No credentials reach JS."""
import base64
import hashlib
import json
import os
import secrets
import re
from urllib.parse import urlencode
from pathlib import Path
from uuid import UUID
from cryptography.fernet import Fernet, InvalidToken
import requests
from django.db import transaction
from django.utils import timezone
from django.views import View
from User.models import ChronelleAccount, ChronelleCredential
from Config.models import Config, CI
from User.chronelle_views import authorization_site, APP_ORIGINS, BASE, MIRROR_BASE
from Chat.research import fail
from utils import auth


class BindingFailure(Exception):
    def __init__(self, code, status=409):
        self.code = {'authorization_revoked':'binding_revoked','authorization_expired':'binding_expired',
            'authorization_required':'binding_required','invalid_grant':'binding_required','refresh_replayed':'binding_required',
            'refresh_expired':'binding_required','invalid_refresh_token':'binding_required','access_denied':'authorization_denied',
            'application_approval_required':'application_not_allowed','application_approval_revoked':'application_not_allowed',
            'invalid_client':'integration_not_configured','application_disabled':'application_not_allowed',
            'invalid_display_token':'calendar_permission_denied','display_origin_not_allowed':'calendar_permission_denied'}.get(code,code)
        self.status = status


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
    client = Config.get_value_by_key(CI.CHRONELLE_CLIENT_ID) or os.environ.get('CHRONELLE_BINDING_CLIENT_ID', saved.get('client_id',''))
    secret = Config.get_value_by_key(CI.CHRONELLE_CLIENT_SECRET) or os.environ.get('CHRONELLE_BINDING_CLIENT_SECRET', saved.get('client_secret',''))
    key = Config.get_value_by_key(CI.CHRONELLE_STORAGE_KEY) or os.environ.get('CHRONELLE_BINDING_STORAGE_KEY', saved.get('storage_key',''))
    if not all((client, secret, key)):
        raise BindingFailure('integration_not_configured', 503)
    try:
        if not re.fullmatch(r'coapp_[A-Za-z0-9_-]{16,80}',client):
            raise ValueError()
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


def remote(base, path, *, method='POST', body=None, token=None, key=None, params=None, origin=None):
    if base not in (BASE, MIRROR_BASE) or not path.startswith('/api/oauth/'):
        raise BindingFailure('integration_not_configured', 503)
    client, secret, _ = config()
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if key:
        headers['Idempotency-Key'] = key
    if origin:
        if origin not in APP_ORIGINS:
            raise BindingFailure('origin_not_allowed',403)
        headers['Origin'] = origin
    try:
        response = requests.request(method, base + path, json=body, headers=headers,
            auth=None if token else (client, secret), params=params, timeout=15, allow_redirects=False)
        value = response.json() if response.content else {}
    except (requests.RequestException, ValueError):
        raise BindingFailure('chronelle_unavailable', 503)
    if not 200 <= response.status_code < 300:
        code = value.get('code', 'chronelle_unavailable') if isinstance(value, dict) else 'chronelle_unavailable'
        # Only protocol codes, never upstream bodies, tokens or translated prose.
        # Upstream 401 means Chronelle authority expired, not Frienden login.
        status=409 if response.status_code==401 else response.status_code if response.status_code in (400,403,409,410,429) else 503
        raise BindingFailure(code if isinstance(code, str) and code.replace('_', '').isalnum() else 'chronelle_unavailable',status)
    if path.endswith(('/token/','/introspect/')) and not isinstance(value,dict):
        raise BindingFailure('chronelle_unavailable',503)
    return value


def authority(value):
    binding = value['binding']
    key = hashlib.sha256((binding['client_id']+'\0'+binding['subject']).encode()).hexdigest()
    row = ChronelleCredential.objects.filter(pk=key).first()
    if row is not None:
        return row
    # Bootstrap existing accounts from the newest verified grant, never an older
    # space's refresh token. No identity is inferred from names or email addresses.
    candidates = [value]
    for account in ChronelleAccount.objects.all().iterator():
        try:
            candidate = unpack(account)
        except BindingFailure:
            continue
        other = candidate.get('binding', {})
        if candidate.get('protocol') == 'oauth-v1' and other.get('client_id') == binding['client_id'] and other.get('subject') == binding['subject']:
            candidates.append(candidate)
    latest = max(candidates, key=lambda item: (item['binding'].get('binding_version', 0), item.get('access_until', 0)))
    sealed = config()[2].encrypt(json.dumps(latest).encode()).decode()
    return ChronelleCredential.objects.get_or_create(pk=key, defaults={'sealed': sealed})[0]


def require_ready(value):
    if value.get('renewal_uncertain'):
        if value.get('renewal_pending_until', 0) > timezone.now().timestamp():
            raise BindingFailure('chronelle_unavailable', 503)
        raise BindingFailure('binding_required')
    if value.get('revoked'):
        raise BindingFailure('binding_required')


def credentials(user, base):
    """Serialize rotating refresh. A lost response is not retried with the old pair."""
    renewal = None
    with transaction.atomic():
        row, _ = ChronelleAccount.objects.get_or_create(user=user)
        row = ChronelleAccount.objects.select_for_update().get(pk=row.pk)
        value = unpack(row)
        if value.get('protocol') != 'oauth-v1' or not value.get('binding') or value['binding'].get('client_id')!=config()[0]:
            raise BindingFailure('binding_required')
        shared = authority(value)
        shared = ChronelleCredential.objects.select_for_update().get(pk=shared.pk)
        value = unpack(shared)
        require_ready(value)
        if value.get('access_until', 0) <= timezone.now().timestamp() + 30:
            # Check this exact refresh credential before consuming it. A stale
            # space must never replay an old pair and revoke a newer grant.
            status = remote(base, '/api/oauth/introspect/', body={'token': value['refresh_token']})
            if type(status.get('active')) is not bool:
                raise BindingFailure('chronelle_unavailable', 503)
            if not status['active']:
                raise BindingFailure('binding_required')
            # Commit uncertainty before the one-shot request, including process crashes.
            value['renewal_uncertain'] = True
            value['renewal_pending_until'] = timezone.now().timestamp() + 60
            store(shared, value)
            renewal = value['refresh_token']
    if renewal:
        try:
            fresh = remote(base, '/api/oauth/token/', body={'grant_type':'refresh_token','refresh_token':renewal})
            with transaction.atomic():
                shared = ChronelleCredential.objects.select_for_update().get(pk=shared.pk)
                current = unpack(shared)
                if current.get('refresh_token') != renewal:
                    raise BindingFailure('binding_superseded')
                binding=validated_binding(fresh)
                if binding['subject']!=current['binding']['subject'] or binding['binding_id']!=current['binding']['binding_id']:
                    raise BindingFailure('binding_required')
                value=current
                value.update(binding=binding,access_token=fresh['access_token'], refresh_token=fresh['refresh_token'],
                    access_until=timezone.now().timestamp()+fresh['expires_in'], renewal_uncertain=False, renewal_pending_until=0)
                store(shared,value)
        except (BindingFailure, KeyError, TypeError) as error:
            with transaction.atomic():
                shared = ChronelleCredential.objects.select_for_update().get(pk=shared.pk)
                current = unpack(shared)
                if current.get('refresh_token') == renewal:
                    current['renewal_pending_until'] = 0
                    store(shared, current)
            if isinstance(error, BindingFailure) and error.code in ('chronelle_unavailable', 'binding_superseded'):
                raise
            raise BindingFailure('binding_required')
    return value


def validated_binding(fresh):
    if not isinstance(fresh,dict) or fresh.get('authorization_type') != 'account_binding' or type(fresh.get('grant_version')) is not int or fresh['grant_version'] < 1:
        raise BindingFailure('invalid_binding',403)
    if any(not isinstance(fresh.get(key),str) or not 1<=len(fresh[key])<=512 for key in ('access_token','refresh_token')) or type(fresh.get('expires_in')) is not int or not 1<=fresh['expires_in']<=900:
        raise BindingFailure('invalid_binding',403)
    from django.utils.dateparse import parse_datetime
    expiry=parse_datetime(fresh.get('grant_expires_at',''))
    if expiry is None or timezone.is_naive(expiry) or expiry <= timezone.now():
        raise BindingFailure('invalid_binding',403)
    return {'subject':str(UUID(fresh['subject'])),'binding_id':str(UUID(fresh['grant_id'])),
        'client_id':config()[0],'binding_version':fresh['grant_version'],'expires_at':expiry.timestamp()}


def claim(user, origin, base, body):
    with transaction.atomic():
        row, _ = ChronelleAccount.objects.get_or_create(user=user)
        row = ChronelleAccount.objects.select_for_update().get(pk=row.pk)
        value = unpack(row)
        pending = value.get('pending', {})
        if pending.get('protocol') != 'oauth-v1' or pending.get('until',0) <= timezone.now().timestamp() or pending.get('origin') != origin:
            return {'status':'expired'}
        if not isinstance(body.get('state'),str) or not secrets.compare_digest(body['state'],pending['state']):
            raise BindingFailure('invalid_state',403)
        if body.get('error'):
            value.pop('pending',None);store(row,value)
            return {'status':'access_denied'}
        if not isinstance(body.get('code'),str) or not 1 <= len(body['code']) <= 200:
            raise BindingFailure('invalid_request',400)
        value.pop('pending',None)
        value['claim_id'] = pending['state']
        store(row,value)
    fresh = remote(pending['base'], '/api/oauth/token/', body={'grant_type':'authorization_code','code':body['code'],'code_verifier':pending['verifier'],'redirect_uri':pending['redirect']})
    binding = validated_binding(fresh)
    with transaction.atomic():
        row = ChronelleAccount.objects.select_for_update().get(user=user)
        if unpack(row).get('claim_id') != pending['state']:
            raise BindingFailure('binding_required')
        value = {'protocol':'oauth-v1','binding':binding,'access_token':fresh['access_token'],'refresh_token':fresh['refresh_token'],'access_until':timezone.now().timestamp()+fresh['expires_in']}
        store(row,value)
        shared = authority(value)
        shared = ChronelleCredential.objects.select_for_update().get(pk=shared.pk)
        if unpack(shared).get('binding', {}).get('binding_version', 0) <= binding['binding_version']:
            store(shared, value)
        from Chat.calendar_policy import initialize
        initialize(user, binding['subject'])
        user.chronelle_calendar_id='';user.chronelle_authorization={}
        user.save(update_fields=['chronelle_calendar_id','chronelle_authorization'])
    return {'status':'active'}


def calendar(user, base, resource, *, method='GET', body=None, key=None):
    if resource not in ('groups/', 'identity/', 'invitations/'):
        raise BindingFailure('calendar_permission_denied', 403)
    value = credentials(user, base)
    result=remote(base, '/api/oauth/calendar/'+resource, method=method, body=body, token=value['access_token'], key=key)
    if resource=='groups/' and (not isinstance(result,list) or any(not isinstance(g,dict) or type(g.get('id')) is not int or not isinstance(g.get('name'),str) for g in result)):
        raise BindingFailure('chronelle_unavailable',503)
    return result


def revocation_credentials(user,base):
    row=ChronelleAccount.objects.filter(user=user).first()
    if row is None or unpack(row).get('protocol') != 'oauth-v1':
        return {'revoked':True}
    try:
        return credentials(user,base)
    except BindingFailure:
        row=ChronelleAccount.objects.filter(user=user).first()
        value=unpack(row) if row else {}
        if value.get('revoked'):
            return {'revoked':True}
        if value.get('binding'):
            status=remote(base,'/api/oauth/introspect/',body={'subject':value['binding']['subject']})
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
            if value.get('pending',{}).get('protocol')=='oauth-v1' and value['pending'].get('until',0)>timezone.now().timestamp():
                return {'status':'pending'}
            if value.get('protocol') != 'oauth-v1' or not value.get('binding') or value['binding'].get('client_id')!=config()[0]:
                return {'status':'binding_required','rebind_required':not value.get('revoked') and bool(row and row.sealed or request.user.chronelle_calendar_id or request.user.chronelle_authorization)}
            value = unpack(authority(value))
            require_ready(value)
            result = remote(base, '/api/oauth/introspect/', body={'subject':value['binding']['subject']})
            if type(result.get('active')) is not bool:
                raise BindingFailure('chronelle_unavailable',503)
            from Chat.calendar_policy import review_required
            return {'status':'active' if result.get('active') else 'binding_required','rebind_required':not result.get('active'),
                    'permissions_review_required':bool(result.get('active')) and review_required(request.user,value['binding']['subject'])}
        except BindingFailure as error:
            return {'status':error.code,'rebind_required':error.code=='binding_required'}

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
                return claim(request.user,origin,base,body)
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
                    if pending.get('protocol')=='oauth-v1' and pending.get('until', 0) > timezone.now().timestamp() and pending.get('origin') == origin:
                        return {'authorize_url':pending['url']}
                    verifier, state = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
                    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                    redirect = origin+'/app/research'
                    url = base+'/oauth/authorize/?'+urlencode({'client_id':config()[0],'response_type':'code','authorization_type':'account_binding','state':state,'code_challenge':challenge,'code_challenge_method':'S256','redirect_uri':redirect})
                    value.pop('claim_id',None)
                    value['pending'] = {'protocol':'oauth-v1','verifier':verifier,'state':state,'redirect':redirect,'origin':origin,'base':base,'url':url,'until':timezone.now().timestamp()+600}
                    store(row, value)
                    return {'authorize_url':url}
                if body.get('action') == 'revoke':
                    if body.get('confirmed') is not True:
                        raise BindingFailure('confirmation_required',400)
                    if value.get('binding'):
                        if value.get('protocol')=='oauth-v1':
                            shared = authority(value)
                            shared = ChronelleCredential.objects.select_for_update().get(pk=shared.pk)
                            current = unpack(shared)
                            remote(base,'/api/oauth/revoke/',body={'token':current['refresh_token']})
                            store(shared,{'revoked':True})
                    store(row,{'revoked':True})
                    request.user.chronelle_calendar_id='';request.user.chronelle_authorization={}
                    request.user.save(update_fields=['chronelle_calendar_id','chronelle_authorization'])
                    return {'status':'binding_required'}
                raise BindingFailure('invalid_request',400)
        except BindingFailure as error:
            return fail(error.code,error.status)
        except (ValueError, KeyError, TypeError):
            return fail('invalid_binding_response',400)
