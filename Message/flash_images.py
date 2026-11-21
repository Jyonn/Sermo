"""One-view image delivery, independent of the reusable media library."""
from base64 import b64encode
from datetime import timedelta
from io import BytesIO

from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from smartdjango import analyse

from Chat.models import ChatMember, ChatMemberStatusChoice
from Message.models import FlashImage, Message, MessageEvent, MessageEventTypeChoice, MessageTypeChoice
from Message.params import MessageParams
from Message.validators import MessageErrors
from User.models import NotificationEvent
from utils import auth


MAX_UPLOAD = 2 * 1024 * 1024
LIFETIME = timedelta(hours=24)


def encode_image(upload):
    if upload is None or not 0 < upload.size <= MAX_UPLOAD:
        raise MessageErrors.PAYLOAD_INVALID
    try:
        with Image.open(upload, formats=('JPEG', 'PNG', 'WEBP')) as image:
            if image.width * image.height > 4_000_000 or getattr(image, 'is_animated', False):
                raise MessageErrors.PAYLOAD_INVALID
            image = ImageOps.exif_transpose(image).convert('RGBA')
            image.thumbnail((1600, 1600))
            canvas = Image.new('RGB', image.size, '#101715')
            canvas.paste(image, mask=image.getchannel('A'))
            full = BytesIO()
            canvas.save(full, format='JPEG', quality=88)
            canvas.thumbnail((10, 10))
            blurred = BytesIO()
            canvas.filter(ImageFilter.GaussianBlur(1.6)).save(blurred, format='JPEG', quality=30)
            return full.getvalue(), b64encode(blurred.getvalue()).decode('ascii')
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise MessageErrors.PAYLOAD_INVALID from None


def flash_payload(message, viewer):
    expiry = message.created_at + LIFETIME
    now = timezone.now()
    available = False
    if viewer is not None and viewer.pk != message.user_id and expiry > now:
        pending = FlashImage.objects.filter(message_id=message.pk, expires_at__gt=now) \
            .values_list('pending_viewers', flat=True).first()
        available = viewer.pk in (pending or [])
    show_thumbnail = available or (viewer is not None and viewer.pk == message.user_id and expiry > now)
    return {
        'kind': 'flash_image',
        'flash_thumbnail': Message._parse_payload(message.content).get('thumbnail') if show_thumbnail else None,
        'flash_available': available,
        'flash_expires_at': expiry.timestamp(),
    }


def private_response(content=b'', status=200):
    response = HttpResponse(content, status=status, content_type='image/jpeg' if status == 200 else 'text/plain')
    response['Cache-Control'] = 'no-store, private, max-age=0'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


class FlashImageSendView(View):
    @auth.require_user
    @analyse.query(MessageParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        chat, user = request.query.chat, request.user
        if chat.submission:
            user.space.require_submission_enabled()
        else:
            user.space.require_chat_enabled()
        if chat.group:
            user.space.require_group_send_allowed(user)
        user.require_capability('chat.message.send.image')
        client_id = request.POST.get('client_message_id', '').strip()
        if not client_id or len(client_id) > Message.vldt.MAX_CLIENT_MESSAGE_ID_LENGTH:
            raise MessageErrors.PAYLOAD_INVALID
        existing = Message.objects.filter(chat=chat, user=user, client_message_id=client_id).first()
        if existing:
            if existing.type != MessageTypeChoice.FLASH_IMAGE or existing.is_deleted:
                raise MessageErrors.PAYLOAD_INVALID
            return existing.jsonl(request=request)
        full, thumbnail = encode_image(request.FILES.get('image'))
        with transaction.atomic():
            recipients = list(ChatMember.objects.filter(
                chat=chat, status=ChatMemberStatusChoice.ACTIVE, user__is_deleted=False,
            ).exclude(user=user).values_list('user_id', flat=True))
            if not recipients:
                raise MessageErrors.PAYLOAD_INVALID
            message = Message.create(chat, user, MessageTypeChoice.FLASH_IMAGE, '',
                                     client_message_id=client_id, flash_thumbnail=thumbnail)
            if message.type != MessageTypeChoice.FLASH_IMAGE or message.is_deleted:
                raise MessageErrors.PAYLOAD_INVALID
            if message._was_created:
                FlashImage.objects.create(message=message, pixels=full, pending_viewers=recipients,
                                          expires_at=message.created_at + LIFETIME)
                if not chat.submission:
                    NotificationEvent.emit_message_notifications(message, actor=user)
        return message.jsonl(request=request)


class FlashImageRevealView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id)
    def post(self, request):
        message, user = request.query.message, request.user
        if (message.type != MessageTypeChoice.FLASH_IMAGE or not message.chat.has_active_member(user)
                or not message.is_visible_to(user)):
            return private_response(status=404)
        if message.chat.submission:
            user.space.require_submission_enabled()
        else:
            user.space.require_chat_enabled()
        if message.chat.group:
            user.space.require_group_join_allowed(user)
        with transaction.atomic():
            item = FlashImage.objects.select_for_update().filter(message_id=message.pk).first()
            if item is None:
                return private_response(status=410)
            if item.expires_at <= timezone.now():
                item.delete()
                return private_response(status=410)
            if user.pk not in item.pending_viewers:
                return private_response(status=410)
            pixels = bytes(item.pixels)
            viewers = [pk for pk in item.pending_viewers if pk != user.pk]
            changed = FlashImage.objects.filter(pk=item.pk, pending_viewers=item.pending_viewers).update(pending_viewers=viewers)
            if not changed:
                return private_response(status=410)
            if not viewers:
                item.delete()
            MessageEvent.objects.create(message=message, chat=message.chat, actor=user,
                                        target_user=user, type=MessageEventTypeChoice.UPDATED)
        return private_response(pixels)
