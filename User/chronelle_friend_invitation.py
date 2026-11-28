import json
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from django.db import transaction
from django.http import JsonResponse
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views import View

from Activity.record_shop import decorate_message
from Chat.calendar_policy import friend_relation
from Chat.models import Chat, ChatPurposeChoice, ChatTypeChoice
from Chat.research import fail
from Chat.research_meetings import validate as validate_meeting
from Message.models import Message, MessageEvent, MessageEventTypeChoice, MessageTypeChoice
from User.chronelle_account import BindingFailure, credentials, remote, resolve_binding, unpack
from User.chronelle_origins import APP_ORIGINS, authorization_site
from User.models import ChronelleAccount, NotificationEvent, User
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


def calendar_groups(base, token):
    groups = remote(base, '/api/oauth/calendar/groups/', method='GET', token=token)
    return [{'id': group['id'], 'name': group['name']} for group in groups]


def publish_invitation_message(message):
    decorate_message(message)
    MessageEvent.record_created(message)
    NotificationEvent.emit_message_notifications(message, actor=message.user)


def deliver_invitation(chat, sender, friend_id, command, invitation_id, proposal):
    content = {
        'kind': 'chronelle_invite',
        'invitation_id': invitation_id,
        'recipient_id': friend_id,
        'status': 'pending',
        **{key: proposal[key] for key in ('title', 'start', 'end', 'timezone')},
    }
    message, created = Message.objects.get_or_create(
        chat=chat, user=sender, client_message_id=f'chronelle-invite-{command}',
        defaults={'type': MessageTypeChoice.CHRONELLE_INVITE, 'content': json.dumps(content, ensure_ascii=False)},
    )
    if message.type != MessageTypeChoice.CHRONELLE_INVITE or Message._parse_payload(message.content).get('invitation_id') != invitation_id:
        raise BindingFailure('invitation_command_conflict', 409)
    if created:
        publish_invitation_message(message)
    return message.pk


class PrivateInvitationView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if isinstance(response, dict):
            response = JsonResponse({'identifier': 'OK', 'body': response})
        response['Cache-Control'] = 'private, no-store'
        return response


class ChronelleFriendInvitationView(PrivateInvitationView):
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
            return {
                'available': True,
                'context': invitation_context(request.user.pk, user_id, bindings),
                'groups': calendar_groups(base, organizer['access_token']),
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
                friend = User.objects.get(pk=user_id, space_id=request.user.space_id, is_deleted=False)
                chat = Chat.get_or_create_direct(request.user, friend)
                chat.require_user_message_allowed(request.user)
                result = remote(
                    base, '/api/oauth/calendar/invitations/', token=organizer['access_token'],
                    key=f'frienden-chat-{command}', body={**proposal, 'subjects': [bindings[1]['subject']], 'client_id': str(command)},
                )
                try:
                    invitation_id = str(UUID(result['id']))
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise BindingFailure('chronelle_unavailable', 503)
                message_id = deliver_invitation(chat, request.user, user_id, command, invitation_id, proposal)
            return {'id': invitation_id, 'message_id': message_id}
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError, KeyError, AttributeError, ZoneInfoNotFoundError):
            return fail('invalid_meeting_request', 400)


def chat_invitation(user, message_id):
    message = Message.objects.select_related('chat').filter(
        pk=message_id, type=MessageTypeChoice.CHRONELLE_INVITE, is_deleted=False,
        chat__space_id=user.space_id, chat__chat_type=ChatTypeChoice.DIRECT,
        chat__purpose=ChatPurposeChoice.NORMAL, chat__is_deleted=False,
    ).first()
    if not message or not message.is_visible_to(user):
        raise BindingFailure('invitation_unavailable', 404)
    payload = Message._parse_payload(message.content)
    if payload.get('response_to') or user.pk not in (message.user_id, payload.get('recipient_id')):
        raise BindingFailure('invitation_unavailable', 404)
    return message, payload


def invitation_detail(event):
    recipient = next((item for item in event['participants'] if not item.get('organizer')), {})
    status = 'cancelled' if event['status'] == 'cancelled' else recipient.get('status', 'unavailable')
    return {
        **{key: event.get(key) for key in ('title', 'start', 'end', 'timezone', 'all_day', 'location', 'description')},
        'status': status,
        'can_respond': event.get('can_respond') is True,
        'proposal_version': event['proposal_version'],
    }


class ChronelleChatInvitationView(PrivateInvitationView):
    @auth.require_user
    def get(self, request, message_id):
        try:
            _, payload = chat_invitation(request.user, message_id)
            base, _ = authorization_site(request)
            token = credentials(request.user, base)['access_token']
            result = remote(base, f"/api/oauth/calendar/invitation/{payload['invitation_id']}/", method='GET', token=token)
            detail = invitation_detail(result)
            if detail['can_respond']:
                detail['groups'] = calendar_groups(base, token)
            return detail
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError, KeyError, AttributeError):
            return fail('chronelle_unavailable', 503)

    @auth.require_user
    def post(self, request, message_id):
        try:
            if request.headers.get('Origin') not in APP_ORIGINS:
                raise BindingFailure('origin_not_allowed', 403)
            if len(request.body) > 4096:
                raise BindingFailure('invalid_request', 413)
            body = json.loads(request.body)
            if not isinstance(body, dict) or set(body) - {'action', 'proposal_version', 'request_key', 'group_id'}:
                raise ValueError()
            command = UUID(body['request_key'])
            action, version = body['action'], body['proposal_version']
            if command.version != 4 or action not in ('accept', 'decline') or type(version) is not int or version < 1:
                raise ValueError()
            response = {'action': action, 'proposal_version': version}
            if action == 'accept':
                if type(body.get('group_id')) is not int or body['group_id'] < 1:
                    raise ValueError()
                response['group_id'] = body['group_id']
            message, payload = chat_invitation(request.user, message_id)
            if request.user.pk != payload['recipient_id']:
                raise BindingFailure('invitation_unavailable', 403)
            base, _ = authorization_site(request)
            token = credentials(request.user, base)['access_token']
            result = remote(base, f"/api/oauth/calendar/respond/{payload['invitation_id']}/", token=token,
                            key=f'frienden-chat-response-{command}', body=response)
            try:
                detail = invitation_detail(result)
                expected_status = 'accepted' if action == 'accept' else 'declined'
                if detail['status'] != expected_status or detail['proposal_version'] != version:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, AttributeError):
                raise BindingFailure('chronelle_unavailable', 503)
            with transaction.atomic():
                message = Message.objects.select_for_update().get(pk=message.pk)
                current = Message._parse_payload(message.content)
                updates = {
                    key: detail[key] for key in ('title', 'start', 'end', 'timezone', 'all_day', 'status')
                    if detail[key] is not None
                }
                updates['response_version'] = version
                if version >= current.get('response_version', 0) and any(
                    current.get(key) != value for key, value in updates.items()
                ):
                    message.content = json.dumps({**current, **updates}, ensure_ascii=False)
                    message.save(update_fields=['content'])
                    MessageEvent.objects.create(message=message, chat=message.chat, actor=request.user, type=MessageEventTypeChoice.UPDATED)
                snapshot = {
                    'kind': 'chronelle_invite', 'response_to': message.pk, 'status': detail['status'],
                    **{key: updates.get(key, payload.get(key)) for key in ('title', 'start', 'end', 'timezone', 'all_day')},
                }
                reply, created = Message.objects.get_or_create(
                    chat=message.chat, user=request.user, client_message_id=f'chronelle-response-{message.pk}-{version}',
                    defaults={'type': MessageTypeChoice.CHRONELLE_INVITE, 'content': json.dumps(snapshot, ensure_ascii=False)},
                )
                if created:
                    publish_invitation_message(reply)
            return detail
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError, KeyError, AttributeError):
            return fail('invalid_request', 400)
