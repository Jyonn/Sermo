from django.views import View
from smartdjango import analyse, OK

from Chat.models import SingleChat, GroupChat, ChatReadState
from Chat.params import GroupChatParams, BaseChatParams, GroupChatMemberParams, GroupChatInviteParams
from User import auth
from User.models import GuestUser, HostUser


class ChatListView(View):
    @staticmethod
    def get_guest_chats(guest: GuestUser):
        chats = []
        chats.extend(SingleChat.objects.filter(guest=guest, is_deleted=False))
        chats.extend(GroupChat.objects.filter(guests=guest, is_deleted=False))
        return chats

    @staticmethod
    def get_host_chats(host: HostUser):
        chats = []
        chats.extend(SingleChat.objects.filter(host=host, is_deleted=False))
        chats.extend(GroupChat.objects.filter(host=host, is_deleted=False))
        return chats

    @staticmethod
    def build_chat_payload(chat, user):
        data = chat.jsonl()
        data['unread_count'] = ChatReadState.unread_count(chat, user)
        last_read_at = ChatReadState.get_last_read_at(chat, user)
        data['last_read_at'] = last_read_at.timestamp() if last_read_at else None
        return data

    @auth.require_user
    def get(self, request):
        user = request.user

        if isinstance(user, HostUser):
            chats = self.get_host_chats(user)
        else:
            chats = self.get_guest_chats(user)
        return [self.build_chat_payload(chat, user) for chat in chats]


class GroupChatView(View):
    @auth.require_user
    @analyse.json(GroupChatParams.guests)
    def post(self, request):
        guests = request.json.guests

        chat = GroupChat.create(request.user, guests)
        return chat.json()

    @auth.require_user
    @analyse.query(GroupChatParams.chat_id)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: GroupChat = request.query.chat
        chat.remove()
        return OK


class GroupChatNameView(View):
    @auth.require_user
    @analyse.query(BaseChatParams.chat_id)
    @analyse.json(GroupChatParams.name)
    @auth.require_chat_owner()
    def post(self, request):
        chat: GroupChat = request.query.chat
        chat.rename(request.json.name)
        return chat.json()


class GroupChatMemberView(View):
    @auth.require_user
    @analyse.query(GroupChatMemberParams.chat_id)
    @analyse.json(GroupChatMemberParams.guests)
    @auth.require_chat_owner()
    def post(self, request):
        chat: GroupChat = request.query.chat
        for guest in request.json.guests:
            chat.invite_guest(request.user, guest)
        return chat.json()

    @auth.require_user
    @analyse.query(GroupChatMemberParams.chat_id)
    @analyse.json(GroupChatMemberParams.guests)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: GroupChat = request.query.chat
        for guest in request.json.guests:
            chat.remove_guest(guest)
        return chat.json()


class GroupChatInviteRespondView(View):
    @auth.require_guest_user
    @analyse.query(GroupChatParams.chat_id)
    @analyse.json(GroupChatInviteParams.accept)
    def post(self, request):
        chat: GroupChat = request.query.chat
        chat.respond_invite(request.user, bool(request.json.accept))
        return chat.json()


class ChatReadView(View):
    @auth.require_user
    @analyse.query(BaseChatParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        state = ChatReadState.mark_read(request.query.chat, request.user)
        return dict(last_read_at=state.last_read_at.timestamp())
