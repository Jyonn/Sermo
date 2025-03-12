from django.views import View

from Message.models import Message
from Message.params import MessageParams
from User import auth
from utils import analyse
from utils.error import OK


class MessageView(View):
    @auth.require_user
    @analyse.query(
        MessageParams.chat_id,
        MessageParams.limit,
        MessageParams.before,
        MessageParams.after,
    )
    @auth.require_chat_member()
    def get(self, request):
        before = request.query.before
        after = request.query.after

        if before is not None:
            return Message.older(request.query.chat, before, request.query.limit)
        if after is not None:
            return Message.newer(request.query.chat, after, request.query.limit)
        return Message.latest(request.query.chat, request.query.limit)

    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @analyse.body(
        MessageParams.content,
        MessageParams.type,
    )
    @auth.require_groupchat_member()
    def post(self, request):
        message = Message.create(
            chat=request.query.chat,
            user=request.user,
            message_type=request.body.type,
            content=request.body.content)
        return message.jsonl()

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    @auth.require_message_owner()
    def delete(self, request):
        message: Message = request.query.message
        message.remove()
        return OK
