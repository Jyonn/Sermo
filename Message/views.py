from django.views import View
from smartdjango import analyse, OK

from Message.models import Message
from Message.params import MessageParams
from User import auth
from User.auth import Request


class MessageView(View):
    @auth.require_user
    @analyse.query(
        MessageParams.chat_id,
        MessageParams.limit,
        MessageParams.before,
        MessageParams.after,
    )
    @auth.require_chat_member()
    def get(self, request: Request):
        print('hello')
        before = request.query.before
        after = request.query.after

        if before is not None:
            return Message.older(request.query.chat, before, request.query.limit)
        if after is not None:
            return Message.newer(request.query.chat, after, request.query.limit)
        return Message.latest(request.query.chat, request.query.limit)

    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @auth.require_chat_member()
    @analyse.json(
        MessageParams.content,
        MessageParams.type,
    )
    def post(self, request: Request):
        message = Message.create(
            chat=request.query.chat,
            user=request.user,
            message_type=request.json.type,
            content=request.json.content)
        return message.jsonl()

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    @auth.require_message_owner()
    def delete(self, request: Request):
        message: Message = request.query.message
        message.remove()
        return OK
