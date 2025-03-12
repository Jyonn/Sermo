from django.views import View

from Chat.models import SingleChat, GroupChat
from Chat.params import GroupChatParams, BaseChatParams
from User import auth
from User.models import GuestUser, HostUser
from utils import analyse
from utils.error import OK


class ChatListView(View):
    @staticmethod
    def get_guest_chats(guest: GuestUser):
        chats = []
        for chat in [SingleChat, GroupChat]:
            chats.extend(chat.get_guest_chats(guest))
        return chats

    @staticmethod
    def get_host_chats(host: HostUser):
        chats = []
        for chat in [SingleChat, GroupChat]:
            chats.extend(chat.get_host_chats(host))
        return chats

    @auth.require_user
    def get(self, request):
        user = request.user

        if isinstance(user, HostUser):
            return self.get_host_chats(user)
        return self.get_guest_chats(user)


class GroupChatView(View):
    @auth.require_host_user
    @analyse.body(GroupChatParams.guests)
    def post(self, request):
        host = request.user
        guests = request.body.guests

        chat = GroupChat.create(host, guests)
        return chat.json()

    @auth.require_host_user
    @analyse.query(GroupChatParams.chat_id)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: GroupChat = request.query.chat
        chat.remove()
        return OK


class GroupChatNameView(View):
    @auth.require_host_user
    @analyse.query(BaseChatParams.chat_id)
    @analyse.body(GroupChatParams.name)
    @auth.require_chat_owner()
    def post(self, request):
        chat: GroupChat = request.query.chat
        chat.rename(request.body.name)
        return chat.json()
