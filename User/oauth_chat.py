"""Narrow delegated chat surface. OAuth bearers never become ordinary user JWTs."""
from django.db import transaction
from django.db.models import Q
from django.views import View

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatPurposeChoice, ChatUserPreference
from Message.models import Message, MessageEvent, MessageEventTypeChoice, MessageTypeChoice
from Message.validators import MessageValidator
from Message.views import _require_message_feature
from User.models import NotificationEvent
from User.oauth import OAuthError
from User.oauth_delegation import require_scope as require_chat_scope
from User.oauth_views import _payload
from utils.content_safety import ContentSafetyScene, check_user_text


def integer(query, key, default=0, maximum=None):
    raw = query.get(key, default)
    if isinstance(raw, bool) or not str(raw).isdigit():
        raise OAuthError('invalid_request', 'Invalid integer parameter')
    value = int(raw)
    if value > 2**63 - 1:
        raise OAuthError('invalid_request', 'Integer parameter too large')
    if maximum is not None and not 1 <= value <= maximum:
        raise OAuthError('invalid_request', 'Parameter outside allowed range')
    return value


def chat_for_user(request, chat_id):
    chat = Chat.objects.filter(pk=chat_id, space_id=request.oauth_grant.target_space_id,
        is_deleted=False, purpose=ChatPurposeChoice.NORMAL).first()
    if not chat or not chat.has_active_member(request.user):
        raise OAuthError('access_denied', 'Conversation unavailable')
    if chat.is_space_group and (not request.user.space.space_group_enabled or not (request.user.is_official or request.user.has_password)):
        raise OAuthError('access_denied', 'Space conversation unavailable')
    _require_message_feature(chat, request.user)
    if chat.group: request.user.space.require_group_join_allowed(request.user)
    return chat


def serialize_message(message):
    # No media payloads, signed downloads, reply bodies, contact details or submission data.
    return dict(message_id=message.id, client_message_id=message.client_message_id,
        user=dict(user_id=message.user_id, name=message.user.name), type=message.type,
        content=message.content if message.type == MessageTypeChoice.TEXT else None,
        created_at=message.created_at.timestamp())


class OAuthChatsView(View):
    @require_chat_scope('chat.read')
    def get(self, request):
        chats = [chat for chat in Chat.get_user_chats(request.user)
                 if chat.space_id == request.oauth_grant.target_space_id]
        result = []
        for chat in chats:
            if chat.group:
                from AccessPolicy.engine import evaluate_capability
                if not evaluate_capability('chat.group.join', user=request.user).allowed: continue
            result.append(dict(chat_id=chat.id, title=chat.title, group=chat.group,
                is_space_group=chat.is_space_group,
                members=[dict(user_id=m.user_id, name=m.user.name) for m in chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE,user__is_deleted=False).select_related('user')]))
        return dict(space_id=request.oauth_grant.target_space_id, chats=result)


class OAuthChatMessagesView(View):
    @require_chat_scope('chat.read')
    def get(self, request, chat_id):
        chat = chat_for_user(request, chat_id)
        limit = integer(request.GET, 'limit', 30, 100)
        before = integer(request.GET, 'before')
        rows = Message.visible_for_user(chat, request.user).select_related('user').order_by('-id')
        if before: rows = rows.filter(id__lt=before)
        rows = list(rows[:limit+1])
        return dict(messages=[serialize_message(m) for m in rows[:limit]], has_more=len(rows)>limit,
            next_before=rows[min(limit,len(rows))-1].id if rows else None)

    @require_chat_scope('chat.write')
    def post(self, request, chat_id):
        chat = chat_for_user(request, chat_id)
        body = _payload(request)
        if set(body) != {'content', 'client_message_id'}:
            raise OAuthError('invalid_request', 'Only text and a client message ID are supported')
        content, key = body['content'], body['client_message_id']
        if not isinstance(content, str) or not content.strip() or len(content)>MessageValidator.MAX_CONTENT_LENGTH:
            raise OAuthError('invalid_request', 'Invalid message text')
        if not isinstance(key, str) or not key.strip() or len(key)>MessageValidator.MAX_CLIENT_MESSAGE_ID_LENGTH:
            raise OAuthError('invalid_request', 'A bounded idempotency key is required')
        if chat.group: request.user.space.require_group_send_allowed(request.user)
        check_user_text(request, content, ContentSafetyScene.COMMENT)
        with transaction.atomic():
            message = Message.create(chat, request.user, MessageTypeChoice.TEXT, content, client_message_id=key)
            if getattr(message, '_was_created', True):
                NotificationEvent.emit_message_notifications(message, actor=request.user)
        return dict(message=serialize_message(message))


class OAuthChatEventsView(View):
    @require_chat_scope('chat.read')
    def get(self, request, chat_id):
        chat = chat_for_user(request, chat_id)
        after = integer(request.GET, 'after')
        limit = integer(request.GET, 'limit', 50, 100)
        rows = MessageEvent.objects.filter(chat=chat, id__gt=after).filter(Q(target_user=request.user)|Q(target_user__isnull=True))
        if chat.group:
            member = ChatMember.objects.get(chat=chat, user=request.user)
            rows = rows.filter(message__created_at__gte=member.joined_at or chat.created_at)
        rows = list(rows.select_related('message','message__user').order_by('id')[:limit+1])
        visible = set(Message.visible_for_user(chat, request.user).filter(pk__in=[r.message_id for r in rows]).values_list('id',flat=True))
        events = []
        event_names = {MessageEventTypeChoice.CREATED: 'message.created', MessageEventTypeChoice.HIDDEN: 'message.hidden',
            MessageEventTypeChoice.RECALLED: 'message.recalled', MessageEventTypeChoice.RESTORED: 'message.restored',
            MessageEventTypeChoice.RELEASED: 'message.created'}
        for row in rows[:limit]:
            event = dict(event_id=row.id, message_id=row.message_id, type=event_names[row.type])
            if row.message_id in visible: event['message'] = serialize_message(row.message)
            events.append(event)
        return dict(events=events, has_more=len(rows)>limit, next_after=rows[min(limit,len(rows))-1].id if rows else after)


class OAuthChatPreferenceView(View):
    @require_chat_scope('chat.modify')
    def post(self, request, chat_id):
        chat = chat_for_user(request, chat_id)
        body = _payload(request)
        # "Modify" affects only this user's pin. It does not include reminders,
        # moderation, group membership, message deletion, or other preferences.
        if set(body) != {'pinned'} or type(body['pinned']) is not bool:
            raise OAuthError('invalid_request', 'Only a boolean pinned field is supported')
        preference = ChatUserPreference.update(chat, request.user, pinned=body['pinned'])
        return dict(chat_id=chat.id, pinned=preference.pinned)
