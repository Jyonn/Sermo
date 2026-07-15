from django.db import transaction
from django.http import HttpResponseRedirect
from django.views import View
from smartdjango import analyse, OK

from Message.models import Message, MessageTypeChoice
from Message.params import MessageParams
from Message.validators import MessageErrors
from utils.qiniu import issue_message_upload, build_message_image_thumbnail_uri, sign_private_download_url
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
            return Message.older(request.query.chat, before, request.query.limit, request=request)
        if after is not None:
            return Message.newer(request.query.chat, after, request.query.limit, request=request)
        return Message.latest(request.query.chat, request.query.limit, request=request)

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
        return message.jsonl(request=request)

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
            request=request,
        )


class MessageBlobView(View):
    @staticmethod
    def _redirect(url: str):
        response = HttpResponseRedirect(url)
        response['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response['Pragma'] = 'no-cache'
        response['Expires'] = '0'
        return response

    def get(self, request: Request, blob_slug: str):
        message = Message.index_by_blob_slug(blob_slug)
        source_uri = message.source_media_uri()
        if not source_uri:
            raise MessageErrors.NOT_EXISTS
        return self._redirect(sign_private_download_url(source_uri))


class MessageBlobThumbnailView(View):
    def get(self, request: Request, blob_slug: str):
        message = Message.index_by_blob_slug(blob_slug)
        if message.type != MessageTypeChoice.IMAGE:
            raise MessageErrors.NOT_EXISTS
        source_uri = message.source_media_uri()
        if not source_uri:
            raise MessageErrors.NOT_EXISTS
        return MessageBlobView._redirect(build_message_image_thumbnail_uri(source_uri))
