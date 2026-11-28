import json
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from django.db import transaction
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views import View

from Chat.calendar_policy import friend_relation
from Chat.research import fail
from Chat.research_meetings import validate as validate_meeting
from User.chronelle_account import BindingFailure, credentials, remote, resolve_binding, unpack
from User.chronelle_origins import APP_ORIGINS, authorization_site
from User.models import ChronelleAccount
from utils import auth
from utils.content_safety import ContentSafetyScene, check_user_text


def invitation_bindings(sender_id, friend_id):
    accounts = {
        account.user_id: account
        for account in ChronelleAccount.objects.filter(user_id__in=(sender_id, friend_id))
    }
    bindings = []
    for user_id in (sender_id, friend_id):
        account = accounts.get(user_id)
        if not account:
            raise BindingFailure('binding_required')
        _, value = resolve_binding(unpack(account))
        bindings.append(value['binding'])
    if bindings[0]['subject'] == bindings[1]['subject']:
        raise BindingFailure('duplicate_calendar_identity', 400)
    return bindings


def invitation_context(sender_id, friend_id, bindings):
    """Bind a draft to these two grants without returning either Chronelle identity."""
    claims = [sender_id, friend_id, *[(grant['client_id'], grant['subject'], grant['binding_id']) for grant in bindings]]
    return salted_hmac('chronelle-friend-invitation', json.dumps(claims), algorithm='sha256').hexdigest()


class ChronelleFriendInvitationView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if isinstance(response, dict):
            response = JsonResponse({'identifier': 'OK', 'body': response})
        response['Cache-Control'] = 'private, no-store'
        return response

    @auth.require_user
    def get(self, request, user_id):
        try:
            if not friend_relation(request.user, user_id).exists():
                raise BindingFailure('friend_unavailable', 404)
            bindings = invitation_bindings(request.user.pk, user_id)
            if request.GET.get('options') != '1':
                return {'available': True}
            base, _ = authorization_site(request)
            organizer = credentials(request.user, base)
            if organizer['binding'] != bindings[0]:
                raise BindingFailure('binding_superseded')
            groups = remote(base, '/api/oauth/calendar/groups/', method='GET', token=organizer['access_token'])
            return {
                'available': True,
                'context': invitation_context(request.user.pk, user_id, bindings),
                'groups': [{'id': group['id'], 'name': group['name']} for group in groups],
            }
        except BindingFailure as error:
            if request.GET.get('options') != '1' and error.code in (
                'friend_unavailable', 'binding_required', 'duplicate_calendar_identity', 'integration_not_configured',
            ):
                return {'available': False}
            return fail(error.code, error.status)
        except (ValueError, TypeError, KeyError):
            return fail('chronelle_unavailable', 503)

    @auth.require_user
    def post(self, request, user_id):
        try:
            if request.headers.get('Origin') not in APP_ORIGINS:
                raise BindingFailure('origin_not_allowed', 403)
            if len(request.body) > 65_536:
                raise BindingFailure('invalid_request', 413)
            body = json.loads(request.body)
            if not isinstance(body, dict) or set(body) - {
                'context', 'command_id', 'title', 'location', 'description', 'start', 'end', 'timezone', 'group_id',
            }:
                raise ValueError()
            sender_id = request.user.pk
            if not friend_relation(request.user, user_id).exists():
                raise BindingFailure('friend_unavailable', 404)
            command, proposal = validate_meeting({**body, 'members': [sender_id, user_id], 'all_day': False}, sender_id)
            del proposal['members']
            bindings = invitation_bindings(sender_id, user_id)
            context = body.get('context')
            if not isinstance(context, str) or not constant_time_compare(context, invitation_context(sender_id, user_id, bindings)):
                raise BindingFailure('binding_superseded')
            check_user_text(request, '\n'.join(proposal[key] for key in ('title', 'description', 'location')), ContentSafetyScene.PROFILE)
            base, _ = authorization_site(request)
            organizer = credentials(request.user, base)
            with transaction.atomic():
                if not friend_relation(request.user, user_id).select_for_update().first():
                    raise BindingFailure('friend_unavailable', 404)
                current = invitation_bindings(sender_id, user_id)
                if current != bindings or organizer['binding'] != bindings[0]:
                    raise BindingFailure('binding_superseded')
                result = remote(
                    base, '/api/oauth/calendar/invitations/', token=organizer['access_token'],
                    key=f'frienden-chat-{command}', body={**proposal, 'subjects': [bindings[1]['subject']], 'client_id': str(command)},
                )
                try:
                    invitation_id = str(UUID(result['id']))
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise BindingFailure('chronelle_unavailable', 503)
            return {'id': invitation_id}
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError, KeyError, AttributeError, ZoneInfoNotFoundError):
            return fail('invalid_meeting_request', 400)
