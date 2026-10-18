"""Registered OAuth-style clients using one-time codes and mandatory PKCE."""

import base64
import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.utils import timezone

from User.models import OAuthGrant, User
from User.oauth_eligibility import eligibility
from User.oauth_registry import DELEGATED_SCOPES, approved
from User.oauth_scopes import LEGACY_SCOPES, COLLABORATION_SCOPES, available_scopes


CODE_LIFETIME = timedelta(minutes=5)
TOKEN_LIFETIME = timedelta(hours=1)
PKCE_RE = re.compile(r'^[A-Za-z0-9_-]{43}$')
VERIFIER_RE = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
MAINLAND_PHONE_RE = re.compile(r'^1[3-9]\d{9}$')


class OAuthError(ValueError):
    def __init__(self, code, description):
        super().__init__(description)
        self.code = code


def require_eligible_user(user):
    if not eligibility(user)['eligible']:
        raise OAuthError('verification_required', 'Verify both your email and phone in account security to use OAuth.')


def registered_redirect_uri(client_id='stillfolio'):
    revision = approved(client_id)
    return revision.redirect_uris[0] if revision else None


def validate_authorization(client_id, redirect_uri, scope, state, code_challenge, code_challenge_method, response_type):
    revision = approved(client_id, redirect_uri) if isinstance(client_id, str) else None
    if not revision:
        raise OAuthError('invalid_request', 'Unknown client or redirect URI')
    if response_type != 'code':
        raise OAuthError('unsupported_response_type', 'Authorization code required')
    if code_challenge_method != 'S256' or not isinstance(code_challenge, str) or not PKCE_RE.fullmatch(code_challenge):
        raise OAuthError('invalid_request', 'PKCE S256 challenge required')
    if not isinstance(state, str) or not 16 <= len(state) <= 512:
        raise OAuthError('invalid_request', 'State must be 16 to 512 characters')
    scopes = set(scope.split()) if isinstance(scope, str) else set()
    if not scopes or not scopes <= set(revision.scopes) or not scopes <= available_scopes():
        raise OAuthError('invalid_scope', 'Unsupported scope')
    return sorted(scopes)


def require_delegation_context(scopes, flow, *, metadata=False):
    delegated = set(scopes) & DELEGATED_SCOPES
    if not delegated:
        return
    if flow is None or flow.purpose not in ('join','space_admin'):
        raise OAuthError('invalid_scope', 'A space-bound authorization request is required')
    if flow.purpose == 'space_admin' and not delegated <= COLLABORATION_SCOPES:
        raise OAuthError('invalid_scope', 'Administrator collaboration permits only chat and contacts')
    if flow.space_id is None and not (metadata and flow.purpose == 'space_admin'):
        raise OAuthError('invalid_scope', 'Select and freeze the space before consent')


def authorize(user, *, client_id, redirect_uri, scope, state, code_challenge, code_challenge_method, response_type, flow=None):
    scopes = validate_authorization(client_id, redirect_uri, scope, state, code_challenge, code_challenge_method, response_type)
    require_delegation_context(scopes, flow)
    code = secrets.token_urlsafe(32)
    with transaction.atomic():
        current_user = User.objects.select_for_update().get(pk=user.pk)
        require_eligible_user(current_user)
        if flow:
            from User.oauth_requests import require_identity
            require_identity(flow, current_user)
            if flow.used_at or flow.cancelled_at or flow.expires_at <= timezone.now():
                raise OAuthError('invalid_request', 'Authorization request is no longer active')
            # Conditional consumption also prevents replay on SQLite.
            if not type(flow).objects.filter(pk=flow.pk, used_at__isnull=True, cancelled_at__isnull=True).update(used_at=timezone.now()):
                raise OAuthError('invalid_request', 'Authorization request already used')
        OAuthGrant.objects.create(
            target_space_id=flow.space_id if flow else None, authorization_request=flow,
            user=current_user, client_id=client_id, redirect_uri=redirect_uri, scopes=scopes,
            code_challenge=code_challenge, code_hash=hashlib.sha256(code.encode()).hexdigest(),
            code_expires_at=timezone.now() + CODE_LIFETIME,
        )
    return f'{redirect_uri}{"&" if "?" in redirect_uri else "?"}{urlencode({"code": code, "state": state})}'


def exchange(*, client_id, redirect_uri, code, code_verifier):
    if not isinstance(client_id, str) or not approved(client_id) or not isinstance(code, str) or not PKCE_RE.fullmatch(code):
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
        current_user = User.objects.select_for_update().get(pk=grant.user_id)
        require_eligible_user(current_user)
        require_grant_identity(grant, current_user)
        token = secrets.token_urlsafe(32)
        grant.code_used_at = timezone.now()
        grant.access_token_hash = hashlib.sha256(token.encode()).hexdigest()
        grant.access_expires_at = timezone.now() + TOKEN_LIFETIME
        grant.save(update_fields=['code_used_at', 'access_token_hash', 'access_expires_at'])
    return {'access_token': token, 'token_type': 'Bearer', 'expires_in': int(TOKEN_LIFETIME.total_seconds()), 'scope': ' '.join(grant.scopes)}


def grant_for_token(token, *, enforce_eligibility=True):
    if not isinstance(token, str) or len(token) > 256 or not token:
        raise OAuthError('invalid_token', 'Invalid access token')
    grant = OAuthGrant.objects.select_related('user', 'user__space').filter(
        access_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        access_expires_at__gt=timezone.now(), revoked_at__isnull=True,
        user__is_deleted=False,
    ).first()
    # Existing grants keep their original consent, even after an approved revision changes.
    if not grant or not approved(grant.client_id):
        raise OAuthError('invalid_token', 'Invalid or expired access token')
    if enforce_eligibility:
        require_eligible_user(grant.user)
        require_grant_identity(grant, grant.user)
    return grant


def userinfo(token):
    grant = grant_for_token(token)
    user = grant.user
    payload = {'sub': f'sermo:{user.space_id}:{user.id}', 'space_id': user.space_id, 'space_slug': user.space.slug}
    if grant.authorization_request_id and grant.authorization_request.purpose == 'space_admin':
        payload['space_admin'] = True
    if 'profile' in grant.scopes:
        payload['nickname'] = user.name
        from User.oauth_avatar import profile_avatar_url
        payload['avatar_url'] = profile_avatar_url(user)
        payload['avatar_updated_at'] = None
    if 'vip' in grant.scopes:
        payload['is_vip'] = bool(user.is_permanent_vip)
    if 'email' in grant.scopes:
        payload['email'] = (user.email or '').strip().lower() or None
        payload['email_verified'] = bool(payload['email'] and user.email_verified_at)
    if 'phone' in grant.scopes:
        raw = (user.phone or '').strip()
        digits = raw[3:] if raw.startswith('+86') else raw[2:] if raw.startswith('86') else raw
        payload['phone'] = f'+86{digits}' if user.phone_verified_at and MAINLAND_PHONE_RE.fullmatch(digits) else None
        payload['phone_verified'] = payload['phone'] is not None
    return payload


def require_grant_identity(grant, user):
    delegated_scopes = set(grant.scopes) - LEGACY_SCOPES
    if delegated_scopes:
        revision = approved(grant.client_id)
        if not revision or not delegated_scopes <= available_scopes() or not delegated_scopes <= set(revision.scopes) or not grant.target_space_id or not grant.authorization_request_id:
            raise OAuthError('invalid_token', 'Delegation is no longer approved')
        require_delegation_context(delegated_scopes, grant.authorization_request)
    if grant.target_space_id and grant.target_space_id != user.space_id:
        raise OAuthError('invalid_grant', 'Space identity changed')
    if grant.authorization_request_id:
        from User.oauth_requests import require_identity
        require_identity(grant.authorization_request, user)


def revoke(token):
    # Losing eligibility must not prevent revoking an existing authorization.
    grant = grant_for_token(token, enforce_eligibility=False)
    grant.revoked_at = timezone.now()
    grant.save(update_fields=['revoked_at'])
