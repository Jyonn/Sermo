from django.db import transaction
from django.views import View
from smartdjango import analyse, OK

from Message.models import Message
from Message.params import MessageParams
from utils.qiniu import issue_message_upload
from utils import auth
from utils.auth import Request
from User.models import NotificationEvent


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
        with transaction.atomic():
            message = Message.create(
                chat=request.query.chat,
                user=request.user,
                message_type=request.json.type,
                content=request.json.content)
            NotificationEvent.emit_message_notifications(message, actor=request.user)
        return message.jsonl()

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    @auth.require_message_owner()
    def delete(self, request: Request):
        message: Message = request.query.message
        message.remove()
        return OK


class MessageUploadView(View):
    @auth.require_user
    @analyse.json(
        MessageParams.kind,
        MessageParams.file_name,
        MessageParams.content_type,
    )
    def post(self, request: Request):
        return issue_message_upload(
            kind=request.json.kind,
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class MessageSyncView(View):
    @auth.require_user
    @analyse.query(
        MessageParams.after,
        MessageParams.limit,
    )
    def get(self, request: Request):
        after = request.query.after or 0
        return Message.sync_for_user(
            user=request.user,
            after=after,
            limit=request.query.limit,
        )
