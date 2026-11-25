"""One-view image delivery, independent of the reusable media library."""
from base64 import b64encode
from datetime import timedelta
from hashlib import sha256
from hmac import new as hmac_digest
from io import BytesIO

import requests
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.db import transaction
from django.db.models import F
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
from utils.qiniu import QINIU_UPLOAD_URL, avatar_uri_for_key, build_upload_token, sign_private_download_url


MAX_UPLOAD = 2 * 1024 * 1024
LIFETIME = timedelta(hours=24)


def store_photo(pixels, chat_id, user_id, client_id):
    scope = f'flash:{chat_id}:{user_id}:{client_id}:{sha256(pixels).hexdigest()}'
    digest = hmac_digest(settings.SECRET_KEY.encode(), scope.encode(), sha256).hexdigest()
    key = f'frienden/flash-photos/{digest}.jpg'
    token = build_upload_token(key, max_file_size=MAX_UPLOAD)
    response = requests.post(QINIU_UPLOAD_URL, data={'key': key, 'token': token},
                             files={'file': ('photo.jpg', pixels, 'image/jpeg')}, timeout=(3, 8))
    response.raise_for_status()
    if response.json().get('key') != key:
        raise ValueError('Invalid storage receipt')
    return key


def read_photo(key):
    uri = sign_private_download_url(avatar_uri_for_key(key), expire_seconds=30)
    with requests.get(uri, allow_redirects=False, stream=True, timeout=(3, 8)) as response:
        if response.status_code != 200:
            raise ValueError('Photo source unavailable')
        pixels = bytearray()
        for chunk in response.iter_content(64 * 1024):
            pixels.extend(chunk)
            if len(pixels) > MAX_UPLOAD:
                raise ValueError('Photo source exceeded upload limit')
        if not pixels.startswith(b'\xff\xd8\xff'):
            raise ValueError('Invalid photo source')
        return bytes(pixels)


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


def delivery_recipients(chat, user):
    return list(ChatMember.objects.filter(
        chat=chat, status=ChatMemberStatusChoice.ACTIVE, user__is_deleted=False,
    ).exclude(user=user).values_list('user_id', flat=True))


def copy_delivery(source, message, user, *, position=-1):
    if source is None or source.user_id != user.pk or not source.is_visible_to(user):
        raise MessageErrors.FORWARD_UNSUPPORTED
    photo = FlashImage.objects.filter(message=source, position=-1).first()
    recipients = delivery_recipients(message.chat, user)
    if photo is None or not recipients:
        raise MessageErrors.FORWARD_UNSUPPORTED
    return FlashImage.objects.create(
        message=message, position=position, storage_key=photo.storage_key, pixels=photo.pixels,
        thumbnail=photo.thumbnail or Message._parse_payload(source.content).get('thumbnail', ''),
        pending_viewers=recipients, viewed_users=[], expires_at=message.created_at + LIFETIME,
    )


def delivery_views(photo, message):
    if photo.viewed_users is not None:
        return photo.viewed_users
    # Older deliveries recorded one targeted update per successful reveal.
    return list(MessageEvent.objects.filter(
        message=message, type=MessageEventTypeChoice.UPDATED, target_user_id=F('actor_id'),
    ).exclude(actor_id=message.user_id).values_list('actor_id', flat=True).distinct())


def delivery_payload(message, viewer, photo):
    result = {
        'kind': 'flash_image',
        'flash_available': False,
        'flash_expires_at': (photo.expires_at if photo else message.created_at + LIFETIME).timestamp(),
    }
    if photo is None or viewer is None:
        return result
    sender = viewer.pk == message.user_id
    viewed_users = delivery_views(photo, message)
    viewed = viewer.pk in viewed_users
    available = sender or (photo.expires_at > timezone.now() and viewer.pk in photo.pending_viewers)
    result.update(flash_available=available, flash_viewed=viewed, flash_sender=sender, flash_thumbnail=None)
    if sender or available:
        result['flash_thumbnail'] = photo.thumbnail or Message._parse_payload(message.content).get('thumbnail')
    if sender:
        result.update(flash_viewed_count=len(viewed_users),
                      flash_recipient_count=len(photo.pending_viewers) + len(viewed_users))
    if photo.position >= 0:
        result.update(flash_message_id=message.pk, flash_position=photo.position)
    return result


def flash_payload(message, viewer):
    photo = None
    if viewer is not None:
        photo = FlashImage.objects.filter(message=message, position=-1).defer('pixels', 'storage_key').first()
    return delivery_payload(message, viewer, photo)


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
        user.require_capability('chat.message.flash_image')
        client_id = request.POST.get('client_message_id', '').strip()
        if not client_id or len(client_id) > Message.vldt.MAX_CLIENT_MESSAGE_ID_LENGTH:
            raise MessageErrors.PAYLOAD_INVALID
        existing = Message.objects.filter(chat=chat, user=user, client_message_id=client_id).first()
        if existing:
            if existing.type != MessageTypeChoice.FLASH_IMAGE or existing.is_deleted:
                raise MessageErrors.PAYLOAD_INVALID
            return existing.jsonl(request=request)
        full, thumbnail = encode_image(request.FILES.get('image'))
        recipients = delivery_recipients(chat, user)
        if not recipients:
            raise MessageErrors.PAYLOAD_INVALID
        try:
            storage_key = store_photo(full, chat.pk, user.pk, client_id)
        except (ValueError, requests.RequestException):
            return private_response(status=503)
        with transaction.atomic():
            message = Message.create(chat, user, MessageTypeChoice.FLASH_IMAGE, '',
                                     client_message_id=client_id, flash_image=True)
            if message.type != MessageTypeChoice.FLASH_IMAGE or message.is_deleted:
                raise MessageErrors.PAYLOAD_INVALID
            if message._was_created:
                FlashImage.objects.create(message=message, pixels=b'', storage_key=storage_key, thumbnail=thumbnail,
                                          pending_viewers=recipients, viewed_users=[], expires_at=message.created_at + LIFETIME)
                if not chat.submission:
                    NotificationEvent.emit_message_notifications(message, actor=user)
        return message.jsonl(request=request)


class FlashImageRevealView(View):
    @auth.require_user
    @analyse.query(MessageParams.message_id, MessageParams.flash_position)
    def post(self, request):
        message, user = request.query.message, request.user
        position = request.query.position
        expected_type = MessageTypeChoice.FLASH_IMAGE if position == -1 else MessageTypeChoice.FORWARD_BUNDLE
        if (message.type != expected_type or not message.chat.has_active_member(user)
                or not message.is_visible_to(user)):
            return private_response(status=404)
        if message.chat.submission:
            user.space.require_submission_enabled()
        else:
            user.space.require_chat_enabled()
        if message.chat.group:
            user.space.require_group_join_allowed(user)
        sender = user.pk == message.user_id
        photos = FlashImage.objects.filter(message_id=message.pk, position=position)
        item = photos.first()
        if item is None or (not sender and (item.expires_at <= timezone.now() or user.pk not in item.pending_viewers)):
            return private_response(status=410)
        try:
            pixels = read_photo(item.storage_key) if item.storage_key else bytes(item.pixels)
        except (ValueError, requests.RequestException):
            return private_response(status=503)
        if sender:
            return private_response(pixels) if message.is_visible_to(user) else private_response(status=404)
        with transaction.atomic():
            item = photos.select_for_update().defer('pixels').filter(expires_at__gt=timezone.now()).first()
            if item is None or user.pk not in item.pending_viewers:
                return private_response(status=410)
            if not message.is_visible_to(user):
                return private_response(status=404)
            viewers = [pk for pk in item.pending_viewers if pk != user.pk]
            views = [*delivery_views(item, message), user.pk]
            changed = FlashImage.objects.filter(pk=item.pk, pending_viewers=item.pending_viewers).update(
                pending_viewers=viewers, viewed_users=views)
            if not changed:
                return private_response(status=410)
            MessageEvent.objects.bulk_create([
                MessageEvent(message=message, chat=message.chat, actor=user,
                             target_user_id=target_id, type=MessageEventTypeChoice.UPDATED)
                for target_id in (user.pk, message.user_id)
            ])
        return private_response(pixels)
