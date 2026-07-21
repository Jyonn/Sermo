from django.views import View
from smartdjango import analyse, OK

from Chat.models import Chat, ChatMember, ChatReadState, ChatUserPreference
from Chat.params import ChatParams, ChatMemberParams, ChatPreferenceParams
from Chat.validators import ChatErrors
from Message.models import Message
from utils import auth


class ChatListView(View):
    @staticmethod
    def build_chat_payload(chat, user, request):
        data = chat.jsonl()
        last_message = Message.visible_in_chat(chat).order_by('-created_at').first()
        if last_message is not None:
            data['last_message'] = last_message.jsonl(request=request)
        data['unread_count'] = ChatReadState.unread_count(chat, user)
        last_read_at = ChatReadState.get_last_read_at(chat, user)
        data['last_read_at'] = last_read_at.timestamp() if last_read_at else None
        preference = ChatUserPreference.objects.filter(chat=chat, user=user).first()
        data['pinned'] = bool(preference and preference.pinned)
        data['online_reminder_enabled'] = bool(preference and preference.online_reminder_enabled)
        return data

    @auth.require_user
    def get(self, request):
        chats = Chat.get_user_chats(request.user)
        payloads = [self.build_chat_payload(chat, request.user, request) for chat in chats]
        payloads.sort(key=lambda item: (bool(item['pinned']), item['last_chat_at']), reverse=True)
        return payloads


class DirectChatView(View):
    @auth.require_user
    @analyse.json(ChatParams.peer_user_id)
    def post(self, request):
        chat = Chat.get_or_create_direct(request.user, request.json.peer_user)
        return chat.json()


class GroupChatView(View):
    @auth.require_user
    @analyse.json(ChatParams.users, ChatParams.title.copy().null().default(None))
    def post(self, request):
        chat = Chat.create_group(request.user, request.json.users, request.json.title)
        return chat.json()

    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: Chat = request.query.chat
        if not chat.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=chat.id)
        chat.remove()
        return OK


class GroupChatNameView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @analyse.json(ChatParams.title)
    @auth.require_chat_member()
    def post(self, request):
        chat: Chat = request.query.chat
        chat.rename(request.json.title)
        return chat.json()


class GroupChatMemberView(View):
    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.users)
    @auth.require_chat_member()
    def post(self, request):
        chat: Chat = request.query.chat
        for user in request.json.users:
            chat.invite_member(request.user, user)
        return chat.json()

    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.users)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: Chat = request.query.chat
        for user in request.json.users:
            chat.remove_member(request.user, user)
        return chat.json()


class GroupChatInviteRespondView(View):
    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.accept)
    def post(self, request):
        chat: Chat = request.query.chat
        chat.respond_invite(request.user, bool(request.json.accept))
        return chat.json()


class GroupChatInviteListView(View):
    @auth.require_user
    def get(self, request):
        return ChatMember.pending_for_user(request.user, limit=100)


class GroupChatLeaveView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        chat: Chat = request.query.chat
        if not chat.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=chat.id)
        chat.leave(request.user)
        return OK


class ChatReadView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        state = ChatReadState.mark_read(request.query.chat, request.user)
        return dict(last_read_at=state.last_read_at.timestamp())


class ChatPreferenceView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def get(self, request):
        return ChatUserPreference.ensure(request.query.chat, request.user).json()

    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @analyse.json(
        ChatPreferenceParams.pinned,
        ChatPreferenceParams.online_reminder_enabled,
    )
    @auth.require_chat_member()
    def post(self, request):
        preference = ChatUserPreference.update(
            request.query.chat,
            request.user,
            pinned=request.json.pinned,
            online_reminder_enabled=request.json.online_reminder_enabled,
        )
        return preference.json()
