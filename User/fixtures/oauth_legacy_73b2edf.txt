"""Registered OAuth-style clients using one-time codes and mandatory PKCE."""

import base64
import hashlib
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.utils import timezone

from User.models import OAuthGrant


SCOPES = frozenset({'profile', 'phone', 'vip'})
CLIENT_SCOPES = {'stillfolio': SCOPES, 'chronelle': frozenset({'profile'}), 'supervision-local': frozenset({'profile'})}
CODE_LIFETIME = timedelta(minutes=5)
TOKEN_LIFETIME = timedelta(hours=1)
PKCE_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
VERIFIER_RE = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
MAINLAND_PHONE_RE = re.compile(r'^1[3-9]\d{9}$')


class OAuthError(ValueError):
    def __init__(self, code, description):
        super().__init__(description)
        self.code = code


def registered_redirect_uri(client_id='stillfolio'):
    if client_id == 'stillfolio':
        return os.environ.get('SERMO_OAUTH_STILLFOLIO_REDIRECT_URI', 'https://stillfolio.jyonn.space/auth/sermo/callback')
    if client_id == 'chronelle':
        return 'https://chronelle.jyonn.space/api/profile/sermo/callback/'
    if client_id == 'supervision-local':
        return 'http://127.0.0.1:8001/api/auth/friendom/callback/'
    return None


def validate_authorization(client_id, redirect_uri, scope, state, code_challenge, code_challenge_method, response_type):
    if not isinstance(client_id, str) or client_id not in CLIENT_SCOPES or redirect_uri != registered_redirect_uri(client_id):
        raise OAuthError('invalid_request', 'Unknown client or redirect URI')
    if response_type != 'code':
        raise OAuthError('unsupported_response_type', 'Authorization code required')
    if code_challenge_method != 'S256' or not isinstance(code_challenge, str) or not PKCE_RE.fullmatch(code_challenge):
        raise OAuthError('invalid_request', 'PKCE S256 challenge required')
    if not isinstance(state, str) or not 16 <= len(state) <= 512:
        raise OAuthError('invalid_request', 'State must be 16 to 512 characters')
    scopes = set(scope.split()) if isinstance(scope, str) else set()
    if not scopes or not scopes <= CLIENT_SCOPES[client_id]:
        raise OAuthError('invalid_scope', 'Unsupported scope')
    return sorted(scopes)


def authorize(user, *, client_id, redirect_uri, scope, state, code_challenge, code_challenge_method, response_type):
    scopes = validate_authorization(client_id, redirect_uri, scope, state, code_challenge, code_challenge_method, response_type)
    code = secrets.token_urlsafe(32)
    OAuthGrant.objects.create(
        user=user, client_id=client_id, redirect_uri=redirect_uri, scopes=scopes,
        code_challenge=code_challenge, code_hash=hashlib.sha256(code.encode()).hexdigest(),
        code_expires_at=timezone.now() + CODE_LIFETIME,
    )
    return f'{redirect_uri}?{urlencode({"code": code, "state": state})}'


def exchange(*, client_id, redirect_uri, code, code_verifier):
    if not isinstance(client_id, str) or client_id not in CLIENT_SCOPES or redirect_uri != registered_redirect_uri(client_id) or not isinstance(code, str) or not PKCE_RE.fullmatch(code):
        raise OAuthError('invalid_grant', 'Invalid authorization code')
    if not isinstance(code_verifier, str) or not VERIFIER_RE.fullmatch(code_verifier):
        raise OAuthError('invalid_grant', 'Invalid PKCE verifier')
    challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b'=').decode()
    with transaction.atomic():
        grant = OAuthGrant.objects.select_for_update().filter(code_hash=hashlib.sha256(code.encode()).hexdigest()).first()
        if (
            not grant or grant.client_id != client_id or grant.redirect_uri != redirect_uri
            or grant.code_used_at or grant.revoked_at or grant.code_expires_at <= timezone.now()
            or not secrets.compare_digest(grant.code_challenge, challenge)
        ):
            raise OAuthError('invalid_grant', 'Invalid or expired authorization code')
        token = secrets.token_urlsafe(32)
        grant.code_used_at = timezone.now()
        grant.access_token_hash = hashlib.sha256(token.encode()).hexdigest()
        grant.access_expires_at = timezone.now() + TOKEN_LIFETIME
        grant.save(update_fields=['code_used_at', 'access_token_hash', 'access_expires_at'])
    return {'access_token': token, 'token_type': 'Bearer', 'expires_in': int(TOKEN_LIFETIME.total_seconds()), 'scope': ' '.join(grant.scopes)}


def grant_for_token(token):
    if not isinstance(token, str) or len(token) > 256 or not token:
        raise OAuthError('invalid_token', 'Invalid access token')
    grant = OAuthGrant.objects.select_related('user', 'user__space').filter(
        access_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        access_expires_at__gt=timezone.now(), revoked_at__isnull=True,
        user__is_deleted=False,
    ).first()
    if not grant:
        raise OAuthError('invalid_token', 'Invalid or expired access token')
    return grant


def userinfo(token):
    grant = grant_for_token(token)
    user = grant.user
    payload = {'sub': f'sermo:{user.space_id}:{user.id}', 'space_slug': user.space.slug}
    if 'profile' in grant.scopes:
        payload['nickname'] = user.name
        from User.oauth_avatar import profile_avatar_url
        payload['avatar_url'] = profile_avatar_url(user)
        payload['avatar_updated_at'] = None
    if 'vip' in grant.scopes:
        payload['is_vip'] = bool(user.is_permanent_vip)
    if 'phone' in grant.scopes:
        raw = (user.phone or '').strip()
        digits = raw[3:] if raw.startswith('+86') else raw[2:] if raw.startswith('86') else raw
        payload['phone'] = f'+86{digits}' if user.phone_verified_at and MAINLAND_PHONE_RE.fullmatch(digits) else None
        payload['phone_verified'] = payload['phone'] is not None
    return payload


def revoke(token):
    grant = grant_for_token(token)
    grant.revoked_at = timezone.now()
    grant.save(update_fields=['revoked_at'])
