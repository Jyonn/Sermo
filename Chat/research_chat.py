"""Read a research conversation without enabling unrelated normal chats."""
from django.views import View

from Chat.models import ChatPurposeChoice
from Chat.research import fail
from Chat.research_calendar import project_for, error_response
from Chat.views import ChatListView
from User.chronelle_account import BindingFailure
from utils import auth


class ResearchChatView(View):
    @auth.require_user
    def get(self, request, project_id):
        if request.GET or not 0 < project_id <= 2 ** 63 - 1:
            return fail('Project unavailable', 404)
        try:
            project = project_for(request.user, project_id)
        except BindingFailure as error:
            return error_response(error)
        chat = project.chat
        if chat.purpose != ChatPurposeChoice.RESEARCH or not chat.group or not chat.has_active_member(request.user):
            return fail('Project unavailable', 404)
        return ChatListView.build_chat_payload(chat, request.user, request)
