from django.db import IntegrityError
from django.http import JsonResponse
from django.views import View

from User.models import OAuthApplication
from User.oauth import OAuthError, validate_authorization, require_delegation_context
from User.oauth_registry import RegistryError, approved, mutate, serialize
from User.oauth_scopes import catalog, permission_groups
from User.oauth_views import _payload, _error
from utils import auth


class OAuthMetadataView(View):
    def get(self, request):
        try:
            flow = None
            params = request.GET
            if 'request_id' in params:
                from User.oauth_requests import resolve
                if set(params) != {'request_id'} or len(params.getlist('request_id')) != 1:
                    raise OAuthError('invalid_request', 'Request parameters are immutable')
                flow = resolve(params['request_id'])
                params = flow.parameters
            elif any(len(request.GET.getlist(key)) != 1 for key in ('client_id', 'redirect_uri', 'scope', 'state', 'code_challenge', 'code_challenge_method', 'response_type')):
                raise OAuthError('invalid_request', 'Provide each authorization parameter exactly once')
            scopes = validate_authorization(**{key: params.get(key, '') for key in (
                'client_id', 'redirect_uri', 'scope', 'state', 'code_challenge', 'code_challenge_method', 'response_type')})
            require_delegation_context(scopes, flow, metadata=True)
            rev = approved(params['client_id'], params['redirect_uri'])
            if not rev:
                raise OAuthError('invalid_request', 'Application is no longer available')
            response = JsonResponse(dict(id=rev.application.client_id, name=rev.name, description=rev.description, icon_url=rev.icon_url,
                                         callback=params['redirect_uri'], scopes=scopes, permission_groups=permission_groups(scopes),
                                         **(dict(parameters=flow.parameters, target_space=dict(id=flow.space_id, slug=flow.space.slug, name=flow.space.name) if flow.space_id else None, purpose=flow.purpose) if flow else {})))
            response['Cache-Control'] = 'no-store'
            return response
        except OAuthError as error:
            return _error(error)


def write(request, admin, app_id):
    try:
        result = mutate(actor=request.platform_admin_email if admin else f'user:{request.user.id}',
                        owner=None if admin else request.user, admin=admin, app_id=app_id, data=_payload(request))
        response = JsonResponse(result)
    except RegistryError as error:
        response = JsonResponse({'error': str(error)}, status=error.status)
    except IntegrityError:
        response = JsonResponse({'error': 'Client ID already exists or application changed'}, status=409)
    response['Cache-Control'] = 'no-store'
    return response


def listing(query):
    response = JsonResponse({'applications': [serialize(app) for app in query.prefetch_related('revisions').order_by('-id')], 'scope_catalog': catalog()})
    response['Cache-Control'] = 'no-store'
    return response


class OAuthDeveloperApplicationsView(View):
    @auth.require_user
    def get(self, request):
        request.user.space.require_oauth_developer()
        return listing(OAuthApplication.objects.filter(owner=request.user))

    @auth.require_user
    def post(self, request, app_id=None):
        request.user.space.require_oauth_developer()
        return write(request, False, app_id)


class OAuthAdminApplicationsView(View):
    @auth.require_platform_admin
    def get(self, request):
        return listing(OAuthApplication.objects.all())

    @auth.require_platform_admin
    def post(self, request, app_id=None):
        return write(request, True, app_id)
