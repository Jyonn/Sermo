"""The current member's personal calendar, separate from research team projections."""
import json
import re
import uuid

from django.views import View

from Chat.research import fail
from Chat.research_calendar import project_for
from Chat.research_status import research_status
from User.chronelle_account import BindingFailure, credentials, remote
from User.chronelle_origins import APP_ORIGINS, authorization_site
from utils import auth


ROUTES = {
    'events': ('GET POST', None, 'start end q group_id category_id'),
    'event': ('GET PATCH DELETE', 'uuid', 'occurrence_start'),
    'move': ('POST', 'uuid', ''),
    'resolve': ('POST', 'uuid', ''),
    'invitations': ('GET POST', None, 'status offset'),
    'invitation': ('GET PATCH', 'uuid', 'occurrence_start'),
    'respond': ('POST', 'uuid', ''),
    'groups': ('GET POST', None, ''),
    'group': ('GET PATCH', 'int', ''),
    'categories': ('GET POST', None, ''),
    'category': ('GET PATCH DELETE', 'int', ''),
    'tasks': ('GET', None, 'q status group_id'),
    'task': ('GET', 'uuid', ''),
    'saved-calendars': ('GET', None, ''),
    'merged': ('GET', None, 'start end spaces'),
    'calendar': ('GET PATCH', None, ''),
    'holidays': ('GET POST DELETE', None, ''),
    'reminder-preferences': ('GET PATCH', None, ''),
    'event-reminder': ('GET PATCH', 'uuid', ''),
    'sync': ('GET PATCH', None, ''),
}


def resource_id(value, kind):
    if kind == 'uuid' and value:
        return str(uuid.UUID(value))
    if kind == 'int' and value and re.fullmatch(r'[1-9][0-9]{0,17}', value):
        return value
    if kind or value:
        raise ValueError()
    return ''


def hide_invitation_share_keys(value):
    if isinstance(value, dict):
        return {key: hide_invitation_share_keys(item) for key, item in value.items()
                if key not in ('share', 'share_token')}
    if isinstance(value, list):
        return [hide_invitation_share_keys(item) for item in value]
    return value


class ResearchChronelleView(View):
    @auth.require_user
    def dispatch(self, request, project_id, resource, identifier=None):
        try:
            methods, kind, allowed_query = ROUTES.get(resource, ('', None, ''))
            if request.method not in methods.split():
                raise BindingFailure('calendar_route_unsupported', 404)
            identifier = resource_id(identifier, kind)
            project = project_for(request.user, project_id)
            query = request.GET.dict()
            if set(query) - set(allowed_query.split()) or any(len(value) > 3000 for value in query.values()):
                raise BindingFailure('invalid_query', 400)
            body = None
            key = None
            if request.method != 'GET':
                if request.headers.get('Origin') not in APP_ORIGINS:
                    raise BindingFailure('origin_not_allowed', 403)
                if research_status(project.status) != 'active':
                    raise BindingFailure('project_read_only', 403)
                if len(request.body) > 65_536:
                    raise BindingFailure('invalid_request', 413)
                envelope = json.loads(request.body or b'{}')
                if not isinstance(envelope, dict) or set(envelope) != {'data', 'request_key'} or not isinstance(envelope['data'], dict):
                    raise BindingFailure('invalid_request', 400)
                key, body = envelope['request_key'], envelope['data']
                if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,128}', key):
                    raise BindingFailure('idempotency_key_required', 400)
            base, _ = authorization_site(request)
            binding = credentials(request.user, base)
            path = '/api/oauth/calendar/' + resource + '/' + (identifier + '/' if identifier else '')
            result = remote(base, path, method=request.method, body=body, token=binding['access_token'], key=key, params=query)
            return hide_invitation_share_keys(result) if resource in ('invitations', 'invitation', 'respond') else result
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (TypeError, ValueError):
            return fail('invalid_request', 400)
