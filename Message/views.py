from django.db import transaction
from django.http import HttpResponseRedirect
from django.views import View
from smartdjango import analyse, OK

from Message.models import ImageMetadata, LinkPreview, Message, MessageTypeChoice, PinnedMessage, VideoMetadata
from Message.params import MessageParams
from Message.validators import MessageErrors
from utils.qiniu import issue_message_upload, build_message_image_thumbnail_uri, build_message_video_thumbnail_uri, sign_private_download_url
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
        MessageParams.reply_to_message_id,
        MessageParams.client_message_id,
    )
    def post(self, request: Request):
        with transaction.atomic():
            message = Message.create(
                chat=request.query.chat,
                user=request.user,
                message_type=request.json.type,
                content=request.json.content,
                reply_to=request.json.reply_to,
                client_message_id=request.json.client_message_id)
            if getattr(message, '_was_created', True):
                NotificationEvent.emit_message_notifications(message, actor=request.user)
        return message.jsonl(request=request)

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    @auth.require_message_owner()
    def delete(self, request: Request):
        message: Message = request.query.message
        message.remove()
        return OK


class MessageBatchView(View):
    @auth.require_user
    @analyse.json(MessageParams.message_ids)
    def delete(self, request: Request):
        message_ids = list(request.json.message_ids)
        with transaction.atomic():
            messages = list(
                Message.objects.select_for_update().filter(
                    id__in=message_ids,
                    user=request.user,
                    is_deleted=False,
                )
            )
            if len(messages) != len(message_ids):
                raise MessageErrors.NOT_EXISTS
            Message.objects.filter(id__in=message_ids).update(is_deleted=True)
        return dict(deleted_message_ids=message_ids)


class PinnedMessageView(View):
    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @auth.require_chat_member()
    def get(self, request: Request):
        return [
            PinnedMessage.aggregate_json(pin, request=request)
            for pin in PinnedMessage.list_for_chat(request.query.chat)
        ]

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def post(self, request: Request):
        PinnedMessage.pin(request.query.message, request.user)
        pin = PinnedMessage.aggregate_for_message(request.query.message)
        return PinnedMessage.aggregate_json(pin, request=request)

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def delete(self, request: Request):
        PinnedMessage.unpin(request.query.message, request.user)
        return OK


class MessageUploadView(View):
    @auth.require_user
    @analyse.json(
        MessageParams.kind,
        MessageParams.file_name,
        MessageParams.content_type,
    )
    def post(self, request: Request):
        capability = {
            'image': 'send_image',
            'audio': 'send_audio',
            'location': 'send_location',
            'video': 'send_video',
        }.get(request.json.kind)
        if capability:
            request.user.require_growth_capability(capability)
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


class MessageLinkPreviewView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def get(self, request: Request):
        message: Message = request.query.message
        if not message.chat.has_active_member(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if message.type != MessageTypeChoice.TEXT:
            return dict(status='none')
        link_preview = LinkPreview.queue_for_text(message.content)
        if link_preview is None:
            return dict(status='none')
        return link_preview.jsonl()


class MessageImageMetadataView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def get(self, request: Request):
        message: Message = request.query.message
        if not message.chat.has_active_member(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if message.type != MessageTypeChoice.IMAGE:
            raise MessageErrors.TYPE_INVALID
        metadata = ImageMetadata.objects.filter(message=message).first()
        if metadata is None:
            metadata = ImageMetadata.queue_for_message(message)
        return metadata.jsonl()


class MessageVideoMetadataView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def get(self, request: Request):
        message: Message = request.query.message
        if not message.chat.has_active_member(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if message.type != MessageTypeChoice.VIDEO:
            raise MessageErrors.TYPE_INVALID
        metadata = VideoMetadata.objects.filter(message=message).first()
        if metadata is None:
            metadata = VideoMetadata.queue_for_message(message)
        return metadata.jsonl()


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
        if message.type not in (MessageTypeChoice.IMAGE, MessageTypeChoice.VIDEO):
            raise MessageErrors.NOT_EXISTS
        source_uri = message.source_media_uri()
        if not source_uri:
            raise MessageErrors.NOT_EXISTS
        if message.type == MessageTypeChoice.VIDEO:
            return MessageBlobView._redirect(build_message_video_thumbnail_uri(source_uri))
        return MessageBlobView._redirect(build_message_image_thumbnail_uri(source_uri))
