"""Shared bearer policy for the five delegated action domains."""
from functools import wraps

from django.db import IntegrityError
from django.http import JsonResponse
from smartdjango import Error

from User.oauth import OAuthError, grant_for_token
from User.oauth_views import _bearer, _error


def require_scope(scope):
    def decorate(method):
        @wraps(method)
        def wrapped(self, request, *args, **kwargs):
            try:
                grant = grant_for_token(_bearer(request))
                if not grant.target_space_id or not grant.authorization_request_id or grant.authorization_request.purpose not in ('join','space_admin'):
                    raise OAuthError('invalid_token', 'Space-bound user consent required')
                if request.headers.get('X-OAuth-Client-ID') != grant.client_id:
                    raise OAuthError('invalid_token', 'Client does not match this delegation')
                if scope not in grant.scopes:
                    raise OAuthError('insufficient_scope', 'Missing delegated permission')
                request.oauth_grant = grant
                request.user = grant.user
                domain = scope.split('.')[0]
                if domain == 'chat': request.user.space.require_chat_enabled()
                if domain == 'submission': request.user.space.require_submission_enabled()
                if domain == 'square': request.user.space.require_square_enabled()
                response = JsonResponse(method(self, request, *args, **kwargs))
                response['Cache-Control'] = 'no-store'
                return response
            except Error:
                return _error(OAuthError('access_denied', 'The current account or space policy does not allow this action'))
            except IntegrityError:
                return _error(OAuthError('invalid_request', 'Conflicting operation; reload before retrying'))
            except OAuthError as error:
                return _error(error)
        return wrapped
    return decorate
