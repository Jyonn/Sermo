"""Immutable, short lived space-bound requests. No client capabilities are granted here."""
import hashlib
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.utils import timezone

from Space.models import Space
from User.models import OAuthAuthorizationRequest
from User.oauth import OAuthError, validate_authorization
from User.oauth_registry import approved

PARAMETERS = ('client_id', 'redirect_uri', 'scope', 'state', 'code_challenge', 'code_challenge_method', 'response_type')


def enabled():
    if not getattr(settings, 'OAUTH_SPACE_REQUESTS_ENABLED', False):
        raise OAuthError('invalid_request', 'Space-bound authorization is not enabled')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def create(data):
    enabled()
    if set(data) - set(PARAMETERS) - {'space_id', 'purpose'}:
        raise OAuthError('invalid_request', 'Unexpected request parameters')
    params = {key: data.get(key, '') for key in PARAMETERS}
    validate_authorization(**params)
    space_id = data.get('space_id')
    space = Space.objects.filter(pk=space_id).first() if type(space_id) is int and space_id > 0 else None
    purpose = data.get('purpose', 'join')
    revision = approved(params['client_id'], params['redirect_uri'])
    bootstrap = purpose == 'space_admin' and 'space_id' not in data
    if (not space and not bootstrap) or purpose not in ('join', 'space_admin'):
        raise OAuthError('invalid_request', 'Valid space and purpose required')
    if purpose == 'space_admin' and not revision.application.space_admin_proof_enabled:
        raise OAuthError('invalid_scope', 'Administrator proof is not approved for this client')
    token = secrets.token_urlsafe(32)
    OAuthAuthorizationRequest.objects.create(token_hash=digest(token), parameters=params, space=space,
        purpose=purpose, expires_at=timezone.now() + timedelta(minutes=30))
    return dict(request_id=token, expires_in=1800,
        authorization_url='https://frienden.jyonn.space/oauth/authorize?' + urlencode({'request_id': token}))


def resolve(token, *, lock=False):
    enabled()
    if not isinstance(token, str) or len(token) != 43:
        raise OAuthError('invalid_request', 'Invalid authorization request')
    query = OAuthAuthorizationRequest.objects.select_for_update() if lock else OAuthAuthorizationRequest.objects
    flow = query.select_related('space').filter(token_hash=digest(token)).first()
    if not flow or flow.used_at or flow.cancelled_at or flow.expires_at <= timezone.now():
        raise OAuthError('invalid_request', 'Authorization request expired, cancelled or used')
    validate_authorization(**flow.parameters)
    return flow


def require_identity(flow, user):
    if user.space_id != flow.space_id:
        raise OAuthError('wrong_space', 'Sign in to the requested space')
    if flow.purpose == 'space_admin':
        revision = approved(flow.parameters['client_id'])
        if not revision or not revision.application.space_admin_proof_enabled or not user.is_official or flow.space.official_user_id != user.id:
            raise OAuthError('admin_required', 'Current space administrator required')


def select_admin_space(flow, user):
    """Only an authenticated official account may select an unbound bootstrap."""
    if flow.space_id is not None:
        return
    if flow.purpose != 'space_admin':
        raise OAuthError('invalid_request', 'A fixed space is required')
    flow.space = Space.objects.select_for_update().get(pk=user.space_id)
    require_identity(flow, user)
    if not type(flow).objects.filter(pk=flow.pk, space__isnull=True, used_at__isnull=True, cancelled_at__isnull=True).update(space_id=user.space_id):
        raise OAuthError('invalid_request', 'Space was already selected')
