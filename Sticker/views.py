from django.http import HttpResponseRedirect
from django.views import View
from smartdjango import OK, analyse

from Message.models import MessageTypeChoice
from Sticker.models import StickerAsset, UserSticker
from Sticker.params import StickerParams
from Sticker.validators import StickerErrors
from utils import auth
from utils.auth import Request
from utils.qiniu import issue_sticker_upload, sign_private_download_url


class StickerView(View):
    @auth.require_user
    def get(self, request: Request):
        return [row.jsonl(request=request) for row in UserSticker.objects.filter(user=request.user).select_related('asset')]

    @auth.require_user
    @analyse.json(StickerParams.message_id)
    def post(self, request: Request):
        message = request.json.message
        if message.type != MessageTypeChoice.IMAGE:
            raise StickerErrors.INVALID_IMAGE
        if not message.chat.has_active_member(request.user):
            raise StickerErrors.NOT_ACCESSIBLE
        try:
            asset, _ = StickerAsset.from_source_uri(
                message.source_media_uri(),
                mime_type=(message._parse_payload(message.content).get('mime_type') or ''),
            )
        except Exception:
            raise StickerErrors.DOWNLOAD_FAILED
        sticker, _ = UserSticker.collect(request.user, asset)
        return sticker.jsonl(request=request)

    @auth.require_user
    @analyse.query(StickerParams.sticker_id)
    def delete(self, request: Request):
        sticker = request.query.sticker
        if sticker.user_id != request.user.id:
            raise StickerErrors.NOT_ACCESSIBLE
        sticker.delete()
        return OK


class StickerPrepareView(View):
    @auth.require_user
    @analyse.json(
        StickerParams.content_hash,
        StickerParams.file_name,
        StickerParams.content_type,
        StickerParams.file_size,
    )
    def post(self, request: Request):
        asset = StickerAsset.objects.filter(content_hash=request.json.content_hash).first()
        if asset is not None:
            sticker, _ = UserSticker.collect(request.user, asset)
            return dict(upload_required=False, sticker=sticker.jsonl(request=request))
        return dict(
            upload_required=True,
            upload=issue_sticker_upload(
                content_hash=request.json.content_hash,
                file_name=request.json.file_name,
                content_type=request.json.content_type,
            ),
        )


class StickerCompleteView(View):
    @auth.require_user
    @analyse.json(
        StickerParams.content_hash,
        StickerParams.storage_key,
        StickerParams.content_type,
        StickerParams.file_size,
    )
    def post(self, request: Request):
        expected_prefix = f'sermo/messages/sticker/{request.json.content_hash}.'
        if not request.json.storage_key.startswith(expected_prefix):
            raise StickerErrors.INVALID_HASH
        asset, _ = StickerAsset.objects.get_or_create(
            content_hash=request.json.content_hash,
            defaults=dict(
                storage_key=request.json.storage_key,
                mime_type=(request.json.content_type or 'image/png')[:100],
                file_size=request.json.file_size,
            ),
        )
        sticker, _ = UserSticker.collect(request.user, asset)
        return sticker.jsonl(request=request)


class StickerAssetView(View):
    def get(self, request, asset_id: int):
        asset = StickerAsset.objects.filter(id=asset_id).first()
        if asset is None:
            raise StickerErrors.NOT_FOUND
        return HttpResponseRedirect(sign_private_download_url(asset.source_uri()))
