from datetime import timedelta

from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.views import View
from smartdjango import analyse, OK

from Chat.models import Chat
from Message.models import ForwardBundle, LinkPreview, MediaAsset, MediaAssetAlias, Message, MessageEvent, MessageHistoryRecovery, MessageTypeChoice, PinnedMessage
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
        request.user.space.require_chat_enabled()
        if request.query.chat.group:
            request.user.space.require_group_join_allowed(request.user)
        before = request.query.before
        after = request.query.after

        if before is not None:
            return Message.older(request.query.chat, before, request.query.limit, request=request, user=request.user)
        if after is not None:
            return Message.newer(request.query.chat, after, request.query.limit, request=request, user=request.user)
        return Message.latest(request.query.chat, request.query.limit, request=request, user=request.user)

    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @auth.require_chat_member()
    @analyse.json(
        MessageParams.content,
        MessageParams.type,
        MessageParams.reply_to_message_id,
        MessageParams.client_message_id,
        MessageParams.mention_user_ids,
    )
    def post(self, request: Request):
        request.user.space.require_chat_enabled()
        if request.json.type in (MessageTypeChoice.SYSTEM, MessageTypeChoice.FORWARD_BUNDLE):
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        if request.query.chat.group:
            request.user.space.require_group_send_allowed(request.user)
        with transaction.atomic():
            message = Message.create(
                chat=request.query.chat,
                user=request.user,
                message_type=request.json.type,
                content=request.json.content,
                reply_to=request.json.reply_to,
                client_message_id=request.json.client_message_id,
                mention_user_ids=request.json.mention_user_ids)
            if getattr(message, '_was_created', True):
                NotificationEvent.emit_message_notifications(message, actor=request.user)
        return message.jsonl(request=request)

    @auth.require_user
    @analyse.query(MessageParams.message_id, MessageParams.delete_scope)
    def delete(self, request: Request):
        message: Message = request.query.message
        if message.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        if not message.is_visible_to(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if request.query.delete_scope == 'me':
            message.hide_for(request.user)
        else:
            if message.user_id != request.user.id:
                raise MessageErrors.NOT_OWNER
            recall_window = timedelta(days=7) if request.user.is_permanent_vip else timedelta(minutes=2)
            if timezone.now() - message.created_at > recall_window:
                raise MessageErrors.RECALL_WINDOW_EXPIRED
            message.remove()
        return OK


class MessageSearchView(View):
    @auth.require_user
    @analyse.query(
        MessageParams.chat_id,
        MessageParams.keyword,
        MessageParams.search_type,
        MessageParams.before,
        MessageParams.limit,
    )
    @auth.require_chat_member()
    def get(self, request: Request):
        return Message.search(
            chat=request.query.chat,
            user=request.user,
            keyword=request.query.keyword,
            message_type=request.query.search_type,
            before=request.query.before,
            limit=request.query.limit,
            request=request,
        )


class MessageBatchView(View):
    @auth.require_user
    @analyse.json(MessageParams.message_ids)
    def delete(self, request: Request):
        message_ids = list(request.json.message_ids)
        with transaction.atomic():
            messages = list(
                Message.objects.select_for_update().select_related('chat').filter(
                    id__in=message_ids,
                    is_deleted=False,
                )
            )
            if len(messages) != len(message_ids):
                raise MessageErrors.NOT_EXISTS
            for message in messages:
                if not message.chat.has_active_member(request.user):
                    raise MessageErrors.NOT_A_MEMBER
                if message.type == MessageTypeChoice.SYSTEM:
                    raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
            for message in messages:
                message.hide_for(request.user)
        return dict(deleted_message_ids=message_ids)


class MessageForwardView(View):
    @auth.require_user
    @analyse.json(MessageParams.message_ids, MessageParams.target_chat_ids, MessageParams.forward_mode)
    def post(self, request: Request):
        request.user.space.require_chat_enabled()
        message_ids = list(request.json.message_ids)
        target_chat_ids = list(request.json.target_chat_ids)
        source_messages = list(
            Message.objects.select_related('chat', 'user', 'media_asset', 'forward_bundle')
            .filter(id__in=message_ids, is_deleted=False)
            .order_by('created_at', 'id')
        )
        if len(source_messages) != len(message_ids):
            raise MessageErrors.NOT_EXISTS
        source_chat_ids = {message.chat_id for message in source_messages}
        if len(source_chat_ids) != 1:
            raise MessageErrors.FORWARD_TARGET_INVALID
        allowed_types = {
            MessageTypeChoice.TEXT, MessageTypeChoice.IMAGE, MessageTypeChoice.FILE,
            MessageTypeChoice.VIDEO, MessageTypeChoice.AUDIO, MessageTypeChoice.LOCATION,
            MessageTypeChoice.STATEMENT, MessageTypeChoice.STICKER,
        }
        for message in source_messages:
            if message.type not in allowed_types:
                raise MessageErrors.FORWARD_UNSUPPORTED
            if not message.is_visible_to(request.user):
                raise MessageErrors.NOT_A_MEMBER

        targets = list(Chat.objects.filter(
            id__in=target_chat_ids,
            space_id=request.user.space_id,
            is_deleted=False,
        ).select_related('space'))
        if len(targets) != len(target_chat_ids):
            raise MessageErrors.FORWARD_TARGET_INVALID
        for target in targets:
            if not target.has_active_member(request.user):
                raise MessageErrors.NOT_A_MEMBER
            if target.group:
                target.space.require_group_send_allowed(request.user)

        created = []
        with transaction.atomic():
            bundle = ForwardBundle.create_from_messages(source_messages, request.user, request=request) \
                if request.json.forward_mode == 'bundle' else None
            for target in targets:
                if bundle is not None:
                    created.append(Message.forward_bundle_message(bundle, target, request.user))
                else:
                    created.extend(Message.forward_individual(source, target, request.user) for source in source_messages)
            for message in created:
                NotificationEvent.emit_message_notifications(message, actor=request.user)
        return dict(messages=[dict(chat_id=message.chat_id, message=message.jsonl(request=request)) for message in created])


class MessageClearView(View):
    @auth.require_user
    @analyse.json(MessageParams.chat_id)
    @auth.require_chat_member()
    def delete(self, request: Request):
        with transaction.atomic():
            deleted_count = Message.clear_for_user(request.json.chat, request.user)
            from Chat.models import ChatReadState
            ChatReadState.mark_read(request.json.chat, request.user)
        return dict(deleted_count=deleted_count)


class MessageHistoryRecoveryView(View):
    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @auth.require_chat_member()
    def get(self, request: Request):
        return MessageHistoryRecovery.status_for(request.query.chat, request.user)

    @auth.require_user
    @analyse.json(MessageParams.chat_id, MessageParams.password)
    @auth.require_chat_member()
    def post(self, request: Request):
        return MessageHistoryRecovery.restore(request.json.chat, request.user, request.json.password)


class MessageReconcileView(View):
    @auth.require_user
    @analyse.json(MessageParams.chat_id, MessageParams.message_ids)
    @auth.require_chat_member()
    def post(self, request: Request):
        requested_ids = set(request.json.message_ids)
        visible_ids = set(
            Message.visible_for_user(request.json.chat, request.user)
            .filter(id__in=requested_ids)
            .values_list('id', flat=True)
        )
        return dict(deleted_message_ids=sorted(requested_ids - visible_ids))


class PinnedMessageView(View):
    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @auth.require_chat_member()
    def get(self, request: Request):
        return [
            PinnedMessage.aggregate_json(pin, request=request)
            for pin in PinnedMessage.list_for_chat(request.query.chat, request.user)
        ]

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def post(self, request: Request):
        if not request.query.message.is_visible_to(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if request.query.message.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        PinnedMessage.pin(request.query.message, request.user)
        pin = PinnedMessage.aggregate_for_message(request.query.message)
        return PinnedMessage.aggregate_json(pin, request=request)

    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def delete(self, request: Request):
        if not request.query.message.is_visible_to(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if request.query.message.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
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
        request.user.space.require_chat_enabled()
        capability = {
            'image': 'chat.message.send.image',
            'audio': 'chat.message.send.audio',
            'location': 'chat.message.send.location',
            'video': 'chat.message.send.video',
        }.get(request.json.kind)
        if capability:
            request.user.require_capability(capability)
        elif request.json.kind == 'file':
            request.user.require_capability('chat.message.send.file')
        return issue_message_upload(
            kind=request.json.kind,
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class MessageEventSyncView(View):
    @auth.require_user
    @analyse.query(MessageParams.after, MessageParams.limit)
    def get(self, request: Request):
        return MessageEvent.sync_for_user(
            user=request.user,
            after=request.query.after or 0,
            limit=request.query.limit,
            request=request,
        )


class MessageLinkPreviewView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def get(self, request: Request):
        message: Message = request.query.message
        if not message.is_visible_to(request.user):
            raise MessageErrors.NOT_A_MEMBER
        if message.type != MessageTypeChoice.TEXT:
            return dict(status='none')
        link_preview = LinkPreview.queue_for_text(message.content)
        if link_preview is None:
            return dict(status='none')
        return link_preview.jsonl()


class MessageMediaMetadataView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def get(self, request: Request):
        message: Message = request.query.message
        if not message.is_visible_to(request.user):
            raise MessageErrors.NOT_A_MEMBER
        kind = {
            MessageTypeChoice.IMAGE: MediaAsset.KIND_IMAGE,
            MessageTypeChoice.VIDEO: MediaAsset.KIND_VIDEO,
        }.get(message.type)
        if kind is None:
            raise MessageErrors.TYPE_INVALID
        metadata = message.media_asset
        if metadata is None:
            metadata = MediaAsset.queue(
                message.source_media_key(), message.source_media_uri(), kind,
            )
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
        asset = MediaAssetAlias.resolve(blob_slug)
        if asset is None or not (
            asset.messages.filter(is_deleted=False).exists()
            or asset.forward_items.filter(bundle__messages__is_deleted=False).exists()
        ):
            raise MessageErrors.NOT_EXISTS
        return self._redirect(sign_private_download_url(asset.source_uri))


class MessageBlobThumbnailView(View):
    def get(self, request: Request, blob_slug: str):
        asset = MediaAssetAlias.resolve(blob_slug)
        if asset is None or asset.kind not in (MediaAsset.KIND_IMAGE, MediaAsset.KIND_VIDEO) or not (
            asset.messages.filter(is_deleted=False).exists()
            or asset.forward_items.filter(bundle__messages__is_deleted=False).exists()
        ):
            raise MessageErrors.NOT_EXISTS
        if asset.kind == MediaAsset.KIND_VIDEO:
            return MessageBlobView._redirect(build_message_video_thumbnail_uri(asset.source_uri))
        return MessageBlobView._redirect(build_message_image_thumbnail_uri(asset.source_uri))
