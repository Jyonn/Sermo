import datetime
import hashlib
import json
import ipaddress
import math
import os
import re
import secrets
import socket
import threading
import uuid
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _, override

from smartdjango import models, Choice

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice
from Message.validators import MessageErrors, MessageValidator
from User.models import User, UserEmojiUsage
from User.validators import UserErrors
from utils import function
from utils.qiniu import sign_private_download_url, avatar_uri_for_key, build_message_image_thumbnail_uri, build_message_video_thumbnail_uri, validate_message_media_key


EARTH_RADIUS_KM = 6371.0088
LOCATION_OBSCURE_RADIUS_KM = 50


def generate_media_blob_slug():
    return uuid.uuid4().hex


def random_point_within_radius(latitude, longitude, radius_km=LOCATION_OBSCURE_RADIUS_KM, rng=None):
    random_source = rng or secrets.SystemRandom()
    distance_km = radius_km * math.sqrt(random_source.random())
    bearing = 2 * math.pi * random_source.random()
    angular_distance = distance_km / EARTH_RADIUS_KM
    latitude_radians = math.radians(latitude)
    longitude_radians = math.radians(longitude)

    randomized_latitude = math.asin(
        math.sin(latitude_radians) * math.cos(angular_distance)
        + math.cos(latitude_radians) * math.sin(angular_distance) * math.cos(bearing)
    )
    randomized_longitude = longitude_radians + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(latitude_radians),
        math.cos(angular_distance) - math.sin(latitude_radians) * math.sin(randomized_latitude),
    )
    normalized_longitude = (math.degrees(randomized_longitude) + 540) % 360 - 180
    return round(math.degrees(randomized_latitude), 6), round(normalized_longitude, 6)


class MessageTypeChoice(Choice):
    TEXT = 0
    IMAGE = 1
    FILE = 2
    SYSTEM = 3
    VIDEO = 4
    AUDIO = 5
    LOCATION = 6
    MAP_ACCESS = 7
    STATEMENT = 8
    STICKER = 9
    FORWARD_BUNDLE = 10


class MessageEventTypeChoice(Choice):
    CREATED = 0
    HIDDEN = 1
    RECALLED = 2
    RESTORED = 3


class LinkPreviewStatusChoice(Choice):
    PENDING = 0
    READY = 1
    FAILED = 2


class LinkPreviewHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_title = False
        self.title_parts = []
        self.meta = {}
        self.icons = []

    def handle_starttag(self, tag, attrs):
        attr_map = {key.lower(): value for key, value in attrs if key and value}
        if tag.lower() == 'title':
            self.in_title = True
        if tag.lower() == 'meta':
            key = (attr_map.get('property') or attr_map.get('name') or '').strip().lower()
            content = (attr_map.get('content') or '').strip()
            if key and content:
                self.meta[key] = content
        if tag.lower() == 'link':
            rel = (attr_map.get('rel') or '').lower()
            href = (attr_map.get('href') or '').strip()
            if href and 'icon' in rel:
                self.icons.append({
                    'href': href,
                    'rel': rel,
                    'sizes': (attr_map.get('sizes') or '').lower(),
                })

    def handle_endtag(self, tag):
        if tag.lower() == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self):
        return ' '.join(''.join(self.title_parts).split())

    @staticmethod
    def _icon_score(icon):
        dimensions = re.findall(r'(\d+)x(\d+)', icon.get('sizes') or '')
        largest_area = max((int(width) * int(height) for width, height in dimensions), default=0)
        touch_priority = 1_000_000 if 'apple-touch-icon' in (icon.get('rel') or '') else 0
        return touch_priority + largest_area

    @property
    def best_icon(self):
        if not self.icons:
            return ''
        return max(self.icons, key=self._icon_score)['href']


class LinkPreview(models.Model):
    URL_RE = re.compile(r'https?://[^\s<>"\'，。！？、；：）】》]+', re.IGNORECASE)
    HTTP_CHARSET_RE = re.compile(r'charset=["\']?([^;"\']+)', re.IGNORECASE)
    HTML_CHARSET_RE = re.compile(br'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9._-]+)', re.IGNORECASE)
    TRAILING_PUNCTUATION = '.,;:!?)]}，。！？、；：）】》'
    MOJIBAKE_MARKERS = ('ï¼', 'ï½', 'ã€', 'Ã', 'Â')
    RETRYABLE_ERROR_MARKERS = ('already consumed',)
    USER_AGENT = (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/138.0.0.0 Safari/537.36'
    )
    BROWSER_HEADERS = {
        'User-Agent': USER_AGENT,
        'Accept': (
            'text/html,application/xhtml+xml,application/xml;q=0.9,'
            'image/avif,image/webp,image/apng,*/*;q=0.8'
        ),
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'Sec-CH-UA': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
        'Sec-CH-UA-Mobile': '?0',
        'Sec-CH-UA-Platform': '"macOS"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1',
    }
    MAX_HTML_BYTES = 256 * 1024
    MAX_REDIRECTS = 3
    READY_TTL = datetime.timedelta(days=7)
    FAILED_TTL = datetime.timedelta(hours=1)
    _FETCHING_IDS = set()
    _FETCHING_LOCK = threading.Lock()

    url = models.URLField(max_length=2048)
    url_hash = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.IntegerField(choices=LinkPreviewStatusChoice.to_choices(), default=LinkPreviewStatusChoice.PENDING, db_index=True)
    title = models.CharField(max_length=255, blank=True, default='')
    description = models.TextField(blank=True, default='')
    image_url = models.URLField(max_length=2048, blank=True, default='')
    site_name = models.CharField(max_length=120, blank=True, default='')
    favicon_url = models.URLField(max_length=2048, blank=True, default='')
    error = models.CharField(max_length=255, blank=True, default='')
    fetched_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def hash_url(cls, url: str):
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    @classmethod
    def extract_first_url(cls, text: str):
        match = cls.URL_RE.search(text or '')
        if not match:
            return None
        raw_url = match.group(0).rstrip(cls.TRAILING_PUNCTUATION)
        return cls.normalize_public_url(raw_url)

    @classmethod
    def normalize_public_url(cls, url: str):
        parsed = urlparse((url or '').strip())
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            return None
        if parsed.username or parsed.password:
            return None
        hostname = parsed.hostname
        if not hostname:
            return None
        cls._require_public_host(hostname)
        normalized = parsed._replace(fragment='')
        return urlunparse(normalized)

    @staticmethod
    def _require_public_host(hostname: str):
        normalized = hostname.strip().strip('.').lower()
        if normalized in ('localhost',):
            raise ValueError('private host')

        try:
            infos = socket.getaddrinfo(normalized, None)
        except socket.gaierror as err:
            raise ValueError('host not resolved') from err

        for info in infos:
            address = info[4][0]
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                raise ValueError('private host')

    @classmethod
    def _clean_text(cls, value: str, limit: int):
        return ' '.join((value or '').split())[:limit]

    @classmethod
    def _safe_absolute_url(cls, base_url: str, value: str):
        if not value:
            return ''
        try:
            return cls.normalize_public_url(urljoin(base_url, value)) or ''
        except ValueError:
            return ''

    @classmethod
    def _decode_html(cls, raw_html: bytes, response):
        candidates = []
        content_type = response.headers.get('Content-Type') or ''
        header_match = cls.HTTP_CHARSET_RE.search(content_type)
        if header_match:
            candidates.append(header_match.group(1).strip())

        meta_match = cls.HTML_CHARSET_RE.search(raw_html[:4096])
        if meta_match:
            candidates.append(meta_match.group(1).decode('ascii', errors='ignore'))

        candidates.extend(['utf-8', response.encoding, 'gb18030', 'gbk', 'big5'])
        seen = set()
        for encoding in candidates:
            normalized = (encoding or '').strip()
            if not normalized or normalized.lower() in seen:
                continue
            seen.add(normalized.lower())
            try:
                return raw_html.decode(normalized)
            except (LookupError, UnicodeDecodeError):
                continue
        return raw_html.decode('utf-8', errors='replace')

    @classmethod
    def _looks_mojibake(cls, *values):
        combined = ' '.join(str(value or '') for value in values)
        return any(marker in combined for marker in cls.MOJIBAKE_MARKERS)

    @classmethod
    def _is_retryable_error(cls, error: str):
        normalized = (error or '').lower()
        return any(marker in normalized for marker in cls.RETRYABLE_ERROR_MARKERS)

    @classmethod
    def _is_expired(cls, preview, now=None):
        if preview.status == LinkPreviewStatusChoice.READY:
            ttl = cls.READY_TTL
        elif preview.status == LinkPreviewStatusChoice.FAILED:
            ttl = cls.FAILED_TTL
        else:
            return False
        return preview.fetched_at is None or preview.fetched_at <= (now or timezone.now()) - ttl

    @classmethod
    def fetch_preview_data(cls, url: str):
        current_url = cls.normalize_public_url(url)
        if not current_url:
            raise ValueError('invalid url')

        response = None
        for _ in range(cls.MAX_REDIRECTS + 1):
            cls.normalize_public_url(current_url)
            response = requests.get(
                current_url,
                headers=cls.BROWSER_HEADERS,
                timeout=(3, 5),
                allow_redirects=False,
                stream=True,
            )
            if 300 <= response.status_code < 400 and response.headers.get('Location'):
                current_url = urljoin(current_url, response.headers['Location'])
                response.close()
                continue
            break

        if response is None:
            raise ValueError('empty response')
        if response.status_code >= 400:
            raise ValueError(f'http {response.status_code}')

        content_type = (response.headers.get('Content-Type') or '').lower()
        if content_type and 'text/html' not in content_type and 'application/xhtml+xml' not in content_type:
            raise ValueError('unsupported content type')

        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= cls.MAX_HTML_BYTES:
                break
        response.close()

        html = cls._decode_html(b''.join(chunks), response)
        parser = LinkPreviewHTMLParser()
        parser.feed(html)

        title = parser.meta.get('og:title') or parser.meta.get('twitter:title') or parser.title
        description = parser.meta.get('og:description') or parser.meta.get('description') or parser.meta.get('twitter:description')
        image_url = parser.meta.get('og:image') or parser.meta.get('twitter:image') or parser.best_icon
        favicon_url = parser.best_icon
        parsed = urlparse(current_url)
        site_name = parser.meta.get('og:site_name') or parsed.hostname or ''

        return dict(
            url=current_url,
            title=cls._clean_text(title or site_name or current_url, 255),
            description=cls._clean_text(description or '', 500),
            image_url=cls._safe_absolute_url(current_url, image_url),
            site_name=cls._clean_text(site_name, 120),
            favicon_url=cls._safe_absolute_url(current_url, favicon_url),
        )

    @classmethod
    def queue_for_text(cls, text: str):
        try:
            url = cls.extract_first_url(text)
        except ValueError:
            return None
        if not url:
            return None

        preview, created = cls.objects.get_or_create(
            url_hash=cls.hash_url(url),
            defaults={'url': url, 'status': LinkPreviewStatusChoice.PENDING},
        )
        force_refresh = cls._is_expired(preview)
        if preview.status == LinkPreviewStatusChoice.READY and cls._looks_mojibake(preview.title, preview.description, preview.site_name):
            preview.status = LinkPreviewStatusChoice.PENDING
            preview.title = ''
            preview.description = ''
            preview.site_name = ''
            preview.image_url = ''
            preview.favicon_url = ''
            preview.error = ''
            preview.save(update_fields=['status', 'title', 'description', 'site_name', 'image_url', 'favicon_url', 'error', 'updated_at'])
        if preview.status == LinkPreviewStatusChoice.FAILED and cls._is_retryable_error(preview.error):
            preview.status = LinkPreviewStatusChoice.PENDING
            preview.error = ''
            preview.save(update_fields=['status', 'error', 'updated_at'])
        if created or preview.status == LinkPreviewStatusChoice.PENDING or force_refresh:
            transaction.on_commit(
                lambda preview_id=preview.id, force=force_refresh: cls.fetch_async(preview_id, force=force),
            )
        return preview

    @classmethod
    def fetch_async(cls, preview_id: int, force=False):
        with cls._FETCHING_LOCK:
            if preview_id in cls._FETCHING_IDS:
                return
            cls._FETCHING_IDS.add(preview_id)
        thread = threading.Thread(target=cls.fetch_and_update, args=(preview_id, force), daemon=True)
        thread.start()

    @classmethod
    def fetch_and_update(cls, preview_id: int, force=False):
        close_old_connections()
        try:
            preview = cls.objects.get(id=preview_id)
            if preview.status == LinkPreviewStatusChoice.READY and not force:
                return
            data = cls.fetch_preview_data(preview.url)
            preview.title = data['title']
            preview.description = data['description']
            preview.image_url = data['image_url']
            preview.site_name = data['site_name']
            preview.favicon_url = data['favicon_url']
            preview.error = ''
            preview.status = LinkPreviewStatusChoice.READY
            preview.fetched_at = timezone.now()
            preview.save(update_fields=[
                'title',
                'description',
                'image_url',
                'site_name',
                'favicon_url',
                'error',
                'status',
                'fetched_at',
                'updated_at',
            ])
        except Exception as err:
            cls.objects.filter(id=preview_id).update(
                status=LinkPreviewStatusChoice.FAILED,
                error=str(err)[:255],
                fetched_at=timezone.now(),
            )
        finally:
            with cls._FETCHING_LOCK:
                cls._FETCHING_IDS.discard(preview_id)
            close_old_connections()

    def jsonl(self):
        status = {
            LinkPreviewStatusChoice.PENDING: 'pending',
            LinkPreviewStatusChoice.READY: 'ready',
            LinkPreviewStatusChoice.FAILED: 'failed',
        }.get(self.status, 'failed')
        return dict(
            url=self.url,
            status=status,
            title=self.title,
            description=self.description,
            image_url=self.image_url,
            site_name=self.site_name,
            favicon_url=self.favicon_url,
        )


class Message(models.Model):
    validators = MessageValidator
    vldt = MessageValidator
    MEDIA_KIND_BY_TYPE = {
        MessageTypeChoice.IMAGE: 'image',
        MessageTypeChoice.FILE: 'file',
        MessageTypeChoice.VIDEO: 'video',
        MessageTypeChoice.AUDIO: 'audio',
    }
    PREVIEW_TEXT_BY_TYPE = {
        MessageTypeChoice.IMAGE: '[图片]',
        MessageTypeChoice.VIDEO: '[视频]',
        MessageTypeChoice.AUDIO: '[语音]',
        MessageTypeChoice.FILE: '[文件]',
        MessageTypeChoice.LOCATION: '[位置]',
        MessageTypeChoice.MAP_ACCESS: '[地图邀请]',
        MessageTypeChoice.STATEMENT: '[发言]',
        MessageTypeChoice.STICKER: '[表情包]',
        MessageTypeChoice.FORWARD_BUNDLE: '[聊天记录]',
    }

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    reply_to = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replies',
    )
    client_message_id = models.CharField(max_length=64, null=True, blank=True)

    type = models.IntegerField(choices=MessageTypeChoice.to_choices())
    content = models.CharField(max_length=vldt.MAX_CONTENT_LENGTH)
    media_asset = models.ForeignKey(
        'MediaAsset', on_delete=models.SET_NULL, null=True, blank=True, related_name='messages',
    )
    forward_bundle = models.ForeignKey(
        'ForwardBundle', on_delete=models.SET_NULL, null=True, blank=True, related_name='messages',
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=['chat', 'user', 'client_message_id'],
                name='message_unique_client_id',
            ),
        ]

    @classmethod
    def visible_queryset(cls):
        return cls.objects.filter(is_deleted=False)

    @classmethod
    def visible_in_chat(cls, chat: Chat):
        return cls.visible_queryset().filter(chat=chat)

    @classmethod
    def visible_for_user(cls, chat: Chat, user: User):
        queryset = cls.visible_in_chat(chat).exclude(hidden_states__user=user)
        if not chat.group:
            return queryset
        from Chat.models import ChatMember, ChatMemberStatusChoice
        membership = ChatMember.objects.filter(
            chat=chat,
            user=user,
            status=ChatMemberStatusChoice.ACTIVE,
        ).only('joined_at').first()
        if membership is None:
            return queryset.none()
        # Legacy active memberships may predate joined_at; the chat creation time
        # is the earliest safe boundary for those rows.
        return queryset.filter(created_at__gte=membership.joined_at or chat.created_at)

    def is_visible_to(self, user: User):
        return self.visible_for_user(self.chat, user).filter(id=self.id).exists()

    @classmethod
    def create(cls, chat: Chat, user: User, message_type, content, reply_to=None, client_message_id=None, mention_user_ids=None):
        if message_type in (MessageTypeChoice.SYSTEM, MessageTypeChoice.FORWARD_BUNDLE):
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        if chat.has_active_member(user):
            if message_type == MessageTypeChoice.TEXT:
                statement_reference = cls.statement_reference_from_text(content, user)
                if statement_reference is not None:
                    message_type = MessageTypeChoice.STATEMENT
                    content = json.dumps(statement_reference, separators=(',', ':'), ensure_ascii=False)
            capability = {
                MessageTypeChoice.IMAGE: 'chat.message.send.image',
                MessageTypeChoice.AUDIO: 'chat.message.send.audio',
                MessageTypeChoice.LOCATION: 'chat.message.send.location',
                MessageTypeChoice.VIDEO: 'chat.message.send.video',
            }.get(message_type)
            if capability:
                user.require_capability(capability)
            elif message_type == MessageTypeChoice.FILE:
                user.require_capability('chat.message.send.file')
            if reply_to is not None and (
                reply_to.chat_id != chat.id
                or reply_to.is_deleted
                or not reply_to.is_visible_to(user)
            ):
                raise MessageErrors.REPLY_TARGET_INVALID
            normalized_client_id = (client_message_id or '').strip()[:cls.vldt.MAX_CLIENT_MESSAGE_ID_LENGTH] or None
            if normalized_client_id:
                existing = cls.objects.filter(
                    chat=chat,
                    user=user,
                    client_message_id=normalized_client_id,
                ).first()
                if existing is not None:
                    existing._was_created = False
                    return existing
            normalized_content = cls.normalize_content(message_type, content)
            if message_type == MessageTypeChoice.STICKER:
                from Sticker.models import StickerAsset, UserSticker
                sticker_payload = cls._parse_payload(content)
                asset = None
                if sticker_payload.get('sticker_id'):
                    sticker = UserSticker.objects.filter(
                        id=sticker_payload.get('sticker_id'),
                        user=user,
                    ).select_related('asset').first()
                    asset = sticker.asset if sticker is not None else None
                elif sticker_payload.get('asset_id'):
                    asset = StickerAsset.objects.filter(id=sticker_payload.get('asset_id')).first()
                if asset is None:
                    raise MessageErrors.PAYLOAD_INVALID
                normalized_content = json.dumps(
                    dict(kind='sticker', asset_id=asset.id),
                    separators=(',', ':'),
                )
            map_access_viewer = None
            if message_type == MessageTypeChoice.MAP_ACCESS:
                payload = cls._parse_payload(normalized_content)
                if not payload.get('chat_grant'):
                    if not chat.direct:
                        raise MessageErrors.MAP_ACCESS_DIRECT_ONLY
                    active_members = list(
                        ChatMember.objects.filter(
                            chat=chat,
                            status=ChatMemberStatusChoice.ACTIVE,
                        ).select_related('user')
                    )
                    peers = [member.user for member in active_members if member.user_id != user.id and not member.user.is_deleted]
                    if len(peers) != 1:
                        raise MessageErrors.MAP_ACCESS_DIRECT_ONLY
                    map_access_viewer = peers[0]
                    target_user_id = payload.get('target_user_id')
                    if target_user_id is not None and int(target_user_id) != map_access_viewer.id:
                        raise MessageErrors.MAP_ACCESS_TARGET_INVALID
            try:
                with transaction.atomic():
                    message = cls.objects.create(
                        chat=chat,
                        user=user,
                        type=message_type,
                        content=normalized_content,
                        reply_to=reply_to,
                        client_message_id=normalized_client_id,
                    )
            except IntegrityError:
                if normalized_client_id is None:
                    raise
                message = cls.objects.get(chat=chat, user=user, client_message_id=normalized_client_id)
                message._was_created = False
                return message
            message._was_created = True
            if message.type == MessageTypeChoice.MAP_ACCESS and map_access_viewer is not None:
                from TravelMap.models import MapAccessGrant
                MapAccessGrant.grant(user, map_access_viewer)
            if message.type in cls.MEDIA_KIND_BY_TYPE:
                payload = cls._parse_payload(message.content)
                message.media_asset = MediaAsset.queue(
                    message.source_media_key(), message.source_media_uri(),
                    MediaAsset.kind_for_name(cls.MEDIA_KIND_BY_TYPE[message.type]),
                    mime_type=payload.get('mime_type'),
                    duration_seconds=payload.get('duration_seconds'),
                    file_size=payload.get('file_size'),
                    file_name=payload.get('file_name'),
                )
                message.save(update_fields=['media_asset'])
            if message.type == MessageTypeChoice.TEXT:
                link_preview = LinkPreview.queue_for_text(message.content)
                UserEmojiUsage.record_text(user, message.content)
                if link_preview is not None:
                    user.award_growth('explore:link')
            exploration_event = {
                MessageTypeChoice.IMAGE: 'explore:image',
                MessageTypeChoice.AUDIO: 'explore:audio',
                MessageTypeChoice.VIDEO: 'explore:video',
                MessageTypeChoice.LOCATION: 'explore:location',
                MessageTypeChoice.FILE: 'explore:file',
                MessageTypeChoice.STICKER: 'explore:sticker_send',
                MessageTypeChoice.MAP_ACCESS: 'explore:map_access',
                MessageTypeChoice.STATEMENT: 'explore:share_statement',
            }.get(message.type)
            if exploration_event:
                user.award_growth(exploration_event)
            if reply_to is not None:
                user.award_growth('explore:message_reply')
            if message.type != MessageTypeChoice.SYSTEM:
                message._award_interaction_growth()
            if message.type == MessageTypeChoice.TEXT and mention_user_ids:
                from Chat.models import ChatMessageMention
                ChatMessageMention.record(message, mention_user_ids)
            MessageEvent.record_created(message)
            return message
        raise MessageErrors.NOT_A_MEMBER

    @classmethod
    def create_system(cls, chat: Chat, user: User, event: str, **details):
        if not chat.has_active_member(user):
            raise MessageErrors.NOT_A_MEMBER
        payload = {
            'kind': 'system',
            'event': str(event).strip(),
            'actor_name': user.name,
            **{key: value for key, value in details.items() if value is not None},
        }
        content = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
        if len(content) > cls.vldt.MAX_CONTENT_LENGTH:
            raise MessageErrors.CONTENT_TOO_LONG
        message = cls.objects.create(
            chat=chat,
            user=user,
            type=MessageTypeChoice.SYSTEM,
            content=content,
        )
        MessageEvent.record_created(message)
        return message

    @classmethod
    def forward_individual(cls, source, chat: Chat, user: User):
        if source.type in (MessageTypeChoice.SYSTEM, MessageTypeChoice.MAP_ACCESS, MessageTypeChoice.FORWARD_BUNDLE):
            raise MessageErrors.FORWARD_UNSUPPORTED
        if not chat.has_active_member(user):
            raise MessageErrors.NOT_A_MEMBER
        message = cls.objects.create(
            chat=chat,
            user=user,
            type=source.type,
            content=source.content,
            media_asset=source.media_asset,
        )
        message._was_created = True
        message._award_interaction_growth()
        MessageEvent.record_created(message)
        return message

    @classmethod
    def forward_bundle_message(cls, bundle, chat: Chat, user: User):
        if not chat.has_active_member(user):
            raise MessageErrors.NOT_A_MEMBER
        message = cls.objects.create(
            chat=chat,
            user=user,
            type=MessageTypeChoice.FORWARD_BUNDLE,
            content=json.dumps(dict(kind='forward_bundle'), separators=(',', ':')),
            forward_bundle=bundle,
        )
        message._was_created = True
        message._award_interaction_growth()
        MessageEvent.record_created(message)
        return message

    @classmethod
    def latest_preview_for_user(cls, chat: Chat, user: User = None):
        queryset = cls.visible_for_user(chat, user) if user is not None else cls.visible_in_chat(chat)
        return queryset.exclude(type=MessageTypeChoice.SYSTEM).order_by('-created_at').first()

    def system_message_text(self, viewer: User = None):
        payload = self._parse_payload(self.content)
        if payload.get('kind') != 'system':
            return _('System message')

        actor = str(payload.get('actor_name') or self.user.name or '').strip()
        member_names = [str(name).strip() for name in payload.get('member_names') or [] if str(name).strip()]
        language = getattr(viewer, 'language', None)
        with override(language):
            names = _('、').join(member_names)
            event = payload.get('event')
            if event == 'group_created':
                if member_names:
                    return _('%(actor)s created the group and invited %(names)s') % dict(actor=actor, names=names)
                return _('%(actor)s created the group') % dict(actor=actor)
            if event == 'members_invited':
                return _('%(actor)s invited %(names)s to the group') % dict(actor=actor, names=names)
            if event == 'members_removed':
                return _('%(actor)s removed %(names)s from the group') % dict(actor=actor, names=names)
            if event == 'member_left':
                return _('%(actor)s left the group') % dict(actor=actor)
            if event == 'group_renamed':
                return _('%(actor)s renamed the group to “%(title)s”') % dict(
                    actor=actor,
                    title=str(payload.get('new_title') or '').strip(),
                )
            if event == 'message_pinned':
                return _('%(actor)s pinned a message') % dict(actor=actor)
            if event == 'message_unpinned':
                return _('%(actor)s unpinned a message') % dict(actor=actor)
            return str(payload.get('text') or _('System message')).strip()

    def _award_interaction_growth(self):
        if not self.user.verified:
            return
        day = timezone.localdate().isoformat()
        week = timezone.localdate().strftime('%G-W%V')
        user = self.user
        user.award_growth(f'daily:first_verified_communication:{day}')

        media_kind = self.MEDIA_KIND_BY_TYPE.get(self.type)
        if media_kind in {'image', 'audio', 'video'} or self.type == MessageTypeChoice.LOCATION:
            normalized_kind = media_kind or 'location'
            user.award_growth(f'daily:verified_media:{day}:user-{user.id}:{normalized_kind}')

        if self.chat.direct:
            peer = User.objects.filter(
                chat_memberships__chat=self.chat,
                chat_memberships__status=ChatMemberStatusChoice.ACTIVE,
                is_deleted=False,
            ).exclude(id=user.id).first()
            if peer is not None and peer.verified:
                user.award_growth(f'daily:different_contact:{day}:user-{peer.id}')
                prior_peer_message = Message.objects.filter(
                    chat=self.chat,
                    user=peer,
                    created_at__date=timezone.localdate(),
                    is_deleted=False,
                ).exclude(id=self.id).exists()
                if prior_peer_message:
                    user.award_growth(f'daily:verified_conversation:{day}:user-{peer.id}')
                    user.award_growth(f'daily:verified_reply:{day}:user-{peer.id}')
        elif self.chat.group:
            user.award_growth(f'daily:verified_group:{day}:chat-{self.chat_id}')

        week_start = timezone.localdate() - datetime.timedelta(days=timezone.localdate().weekday())
        events = list(
            user.growth_events.filter(category='daily', created_at__date__gte=week_start)
            .values_list('event_key', flat=True)
        )
        week_days = {key.split(':')[2] for key in events if key.startswith('daily:first_verified_communication:')}
        if len(week_days) >= 3:
            user.award_growth(f'weekly:active_3_days:{week}')
        if len(week_days) >= 5:
            user.award_growth(f'weekly:active_5_days:{week}')
        if len({key.rsplit(':', 1)[-1] for key in events if key.startswith('daily:different_contact:')}) >= 3:
            user.award_growth(f'weekly:contacts_3:{week}')
        if len({key.rsplit(':', 1)[-1] for key in events if key.startswith('daily:verified_group:')}) >= 2:
            user.award_growth(f'weekly:groups_2:{week}')
        if len({key.rsplit(':', 1)[-1] for key in events if key.startswith('daily:verified_media:')}) >= 3:
            user.award_growth(f'weekly:media_3:{week}')

    @classmethod
    def _parse_payload(cls, content):
        try:
            payload = json.loads(content)
        except (TypeError, ValueError):
            raise MessageErrors.PAYLOAD_INVALID
        if not isinstance(payload, dict):
            raise MessageErrors.PAYLOAD_INVALID
        return payload

    @classmethod
    def _normalize_media_content(cls, message_type, content):
        payload = cls._parse_payload(content)
        kind = cls.MEDIA_KIND_BY_TYPE.get(message_type)
        if not kind:
            raise MessageErrors.TYPE_INVALID

        key = validate_message_media_key(kind, payload.get('key'))
        normalized = dict(
            kind=kind,
            uri=avatar_uri_for_key(key),
        )
        mime_type = (str(payload.get('mime_type') or '').strip())[:100]
        if mime_type:
            normalized['mime_type'] = mime_type
        if message_type == MessageTypeChoice.AUDIO:
            try:
                duration_seconds = float(payload.get('duration_seconds'))
            except (TypeError, ValueError):
                raise MessageErrors.AUDIO_DURATION_INVALID
            if duration_seconds <= 0 or duration_seconds > cls.vldt.MAX_AUDIO_DURATION_SECONDS:
                raise MessageErrors.AUDIO_DURATION_INVALID
            normalized['duration_seconds'] = round(duration_seconds, 1)
        if message_type == MessageTypeChoice.FILE:
            file_name = os.path.basename(str(payload.get('file_name') or '').strip())[:180]
            if not file_name:
                raise MessageErrors.PAYLOAD_INVALID
            try:
                file_size = max(0, int(payload.get('file_size') or 0))
            except (TypeError, ValueError):
                raise MessageErrors.PAYLOAD_INVALID
            normalized['file_name'] = file_name
            normalized['file_size'] = file_size
        return json.dumps(normalized, separators=(',', ':'), ensure_ascii=False)

    @classmethod
    def statement_reference_from_text(cls, content, user):
        from Square.models import Statement

        for match in LinkPreview.URL_RE.finditer(content or ''):
            raw_url = match.group(0).rstrip(LinkPreview.TRAILING_PUNCTUATION)
            parsed = urlparse(raw_url)
            hostname = (parsed.hostname or '').lower()
            trusted_host = (
                hostname in {'sermo.jyonn.space', 'localhost', '127.0.0.1'}
                or hostname.endswith('.sermo.jyonn.space')
                or hostname.endswith('.localhost')
            )
            if not trusted_host:
                continue
            path_match = re.fullmatch(r'/(?:([^/]+)/)?app/square/statements/(\d+)/?', parsed.path)
            if path_match is None:
                continue
            path_slug, statement_id = path_match.groups()
            host_slug = hostname.removesuffix('.sermo.jyonn.space') if hostname.endswith('.sermo.jyonn.space') else ''
            referenced_slug = (path_slug or host_slug or user.space.slug).lower()
            if referenced_slug != user.space.slug:
                continue
            statement = Statement.visible_for(user).filter(id=int(statement_id)).first()
            if statement is None:
                continue
            return dict(kind='statement', statement_id=statement.id, url=raw_url, text=(content or '').strip())
        return None

    @classmethod
    def normalize_content(cls, message_type, content):
        if message_type in (MessageTypeChoice.TEXT, MessageTypeChoice.SYSTEM):
            normalized = (content or '').strip()
            if not normalized:
                raise MessageErrors.CONTENT_EMPTY
            if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                raise MessageErrors.CONTENT_TOO_LONG
            return normalized

        if message_type in cls.MEDIA_KIND_BY_TYPE:
            normalized = cls._normalize_media_content(message_type, content)
            if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                raise MessageErrors.CONTENT_TOO_LONG
            return normalized

        if message_type == MessageTypeChoice.LOCATION:
            payload = cls._parse_payload(content)
            try:
                latitude = round(float(payload.get('latitude')), 6)
                longitude = round(float(payload.get('longitude')), 6)
            except (TypeError, ValueError):
                raise MessageErrors.PAYLOAD_INVALID
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                raise MessageErrors.PAYLOAD_INVALID
            obscure = payload.get('obscure') is True or payload.get('obscure') == 1
            if obscure:
                latitude, longitude = random_point_within_radius(latitude, longitude)

            address = ''
            geocoding_provider = ''
            try:
                from Message.image_metadata import reverse_geocode
                address, geocoding_provider = reverse_geocode(latitude, longitude)
            except Exception:
                pass
            normalized_payload = dict(
                kind='location',
                latitude=latitude,
                longitude=longitude,
                address=address,
                geocoding_provider=geocoding_provider,
            )
            if obscure:
                normalized_payload.update(
                    obscured=True,
                    obscure_radius_km=LOCATION_OBSCURE_RADIUS_KM,
                )
            normalized = json.dumps(
                normalized_payload,
                separators=(',', ':'),
                ensure_ascii=False,
            )
            if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                raise MessageErrors.CONTENT_TOO_LONG
            return normalized

        if message_type == MessageTypeChoice.MAP_ACCESS:
            payload = cls._parse_payload(content)
            if payload.get('chat_grant') is True:
                normalized = json.dumps(
                    dict(
                        kind='map_access',
                        chat_grant=True,
                        message_key='travel_map_join',
                    ),
                    separators=(',', ':'),
                    ensure_ascii=False,
                )
                if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                    raise MessageErrors.CONTENT_TOO_LONG
                return normalized
            try:
                target_user_id = int(payload.get('target_user_id'))
            except (TypeError, ValueError):
                raise MessageErrors.MAP_ACCESS_TARGET_INVALID
            normalized = json.dumps(
                dict(kind='map_access', target_user_id=target_user_id),
                separators=(',', ':'),
            )
            if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                raise MessageErrors.CONTENT_TOO_LONG
            return normalized

        if message_type == MessageTypeChoice.STATEMENT:
            payload = cls._parse_payload(content)
            try:
                statement_id = int(payload.get('statement_id'))
            except (TypeError, ValueError):
                raise MessageErrors.PAYLOAD_INVALID
            normalized = json.dumps(
                dict(
                    kind='statement',
                    statement_id=statement_id,
                    url=(str(payload.get('url') or '').strip())[:280],
                    text=(str(payload.get('text') or '').strip())[:100],
                ),
                separators=(',', ':'),
                ensure_ascii=False,
            )
            if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                raise MessageErrors.CONTENT_TOO_LONG
            return normalized

        if message_type == MessageTypeChoice.STICKER:
            payload = cls._parse_payload(content)
            try:
                asset_id = int(payload.get('asset_id') or payload.get('sticker_id'))
            except (TypeError, ValueError):
                raise MessageErrors.PAYLOAD_INVALID
            normalized = json.dumps(dict(kind='sticker', asset_id=asset_id), separators=(',', ':'))
            if len(normalized) > cls.vldt.MAX_CONTENT_LENGTH:
                raise MessageErrors.CONTENT_TOO_LONG
            return normalized

        raise MessageErrors.TYPE_INVALID

    def _blob_path(self, thumbnail: bool = False):
        if not self.media_asset_id:
            return ''
        route_name = 'message blob thumbnail' if thumbnail else 'message blob'
        return reverse(route_name, kwargs={'blob_slug': self.media_asset.blob_slug})

    def media_blob_uri(self, request: HttpRequest = None, thumbnail: bool = False):
        path = self._blob_path(thumbnail=thumbnail)
        if not path:
            return ''
        if request is None:
            return path
        return request.build_absolute_uri(path)

    @staticmethod
    def _viewer_from_request(request: HttpRequest = None):
        viewer = getattr(request, 'user', None) if request is not None else None
        return viewer if isinstance(viewer, User) else None

    def _payload_for_type(self, request: HttpRequest = None):
        if self.type == MessageTypeChoice.TEXT:
            payload = dict(kind='text', text=self.content)
            link_preview = LinkPreview.queue_for_text(self.content)
            if link_preview is not None:
                payload['link_preview'] = link_preview.jsonl()
            return payload
        if self.type == MessageTypeChoice.SYSTEM:
            payload = self._parse_payload(self.content)
            if payload.get('kind') == 'system':
                payload['text'] = self.system_message_text(
                    self._viewer_from_request(request),
                )
                return payload
            return dict(kind='system', text=self.system_message_text())
        if self.type == MessageTypeChoice.LOCATION:
            return self._parse_payload(self.content)
        if self.type == MessageTypeChoice.MAP_ACCESS:
            from TravelMap.models import MapAccessGrant, MapChatGrant
            payload = self._parse_payload(self.content)
            viewer = self._viewer_from_request(request)
            response = dict(
                kind='map_access',
                owner=self.user.tiny_json(),
                target_user_id=payload.get('target_user_id'),
                chat_grant=bool(payload.get('chat_grant')),
                message_key=payload.get('message_key') or '',
            )
            if viewer is not None and viewer.space_id == self.user.space_id:
                response['chat_access'] = MapChatGrant.status(self.chat, viewer) if response['chat_grant'] else None
                response['access'] = None if response['chat_grant'] else MapAccessGrant.status_between(viewer, self.user)
            return response
        if self.type == MessageTypeChoice.STATEMENT:
            from Square.models import Statement
            reference = self._parse_payload(self.content)
            viewer = self._viewer_from_request(request)
            response = dict(
                kind='statement',
                statement_id=reference.get('statement_id'),
                url=reference.get('url') or '',
                text=reference.get('text') or '',
                statement=None,
            )
            if viewer is not None and Statement.visible_for(viewer).filter(id=reference.get('statement_id')).exists():
                response['statement'] = Statement.detail(viewer, reference.get('statement_id'), request=request)
            return response
        if self.type == MessageTypeChoice.STICKER:
            from Sticker.models import StickerAsset
            reference = self._parse_payload(self.content)
            asset = StickerAsset.objects.filter(id=reference.get('asset_id')).first()
            if asset is None:
                return dict(kind='sticker', unavailable=True)
            response = asset.jsonl(request=request)
            response['kind'] = 'sticker'
            return response
        if self.type == MessageTypeChoice.FORWARD_BUNDLE:
            if self.forward_bundle_id is None:
                return dict(kind='forward_bundle', unavailable=True, items=[])
            return self.forward_bundle.jsonl(request=request)
        if self.type == MessageTypeChoice.FILE and not self.content.lstrip().startswith('{'):
            return dict(kind='file', text=self.content)
        if self.type in self.MEDIA_KIND_BY_TYPE:
            payload = self._parse_payload(self.content)
            uri = (payload.get('uri') or '').strip()
            response = dict(kind=payload.get('kind') or self.MEDIA_KIND_BY_TYPE[self.type])
            if self.media_asset_id:
                response['uri'] = self.media_blob_uri(request=request)
                if self.type in (MessageTypeChoice.IMAGE, MessageTypeChoice.VIDEO):
                    response['thumbnail_uri'] = self.media_blob_uri(request=request, thumbnail=True)
            elif uri:
                response['uri'] = sign_private_download_url(uri)
                if self.type == MessageTypeChoice.IMAGE:
                    response['thumbnail_uri'] = build_message_image_thumbnail_uri(uri)
                elif self.type == MessageTypeChoice.VIDEO:
                    response['thumbnail_uri'] = build_message_video_thumbnail_uri(uri)
            mime_type = (str(payload.get('mime_type') or '').strip())[:100]
            if mime_type:
                response['mime_type'] = mime_type
            if self.type == MessageTypeChoice.AUDIO and 'duration_seconds' in payload:
                response['duration_seconds'] = payload.get('duration_seconds')
            if self.type == MessageTypeChoice.FILE:
                response['file_name'] = payload.get('file_name') or '文件'
                response['file_size'] = payload.get('file_size') or 0
            if self.type == MessageTypeChoice.IMAGE:
                metadata = self.media_asset
                if metadata is not None:
                    response['image_metadata'] = metadata.jsonl()
            elif self.type == MessageTypeChoice.VIDEO:
                metadata = self.media_asset
                if metadata is not None:
                    response['video_metadata'] = metadata.jsonl()
            return response
        return None

    def preview_text(self):
        return self.PREVIEW_TEXT_BY_TYPE.get(self.type, self.content)

    def _dictify_user(self):
        return self.user.tiny_json()

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_content(self):
        return self.preview_text()

    def _reply_to_payload(self, request: HttpRequest = None):
        if self.reply_to_id is None:
            return None
        reply_to = self.reply_to
        viewer = self._viewer_from_request(request)
        if viewer is not None and not Message.visible_for_user(self.chat, viewer).filter(id=reply_to.id).exists():
            return dict(
                message_id=reply_to.id,
                user=None,
                type=reply_to.type,
                content='消息不可见',
                is_deleted=True,
            )
        return dict(
            message_id=reply_to.id,
            user=reply_to.user.tiny_json(),
            type=reply_to.type,
            content='消息已删除' if reply_to.is_deleted else reply_to.preview_text(),
            is_deleted=reply_to.is_deleted,
        )

    def source_media_uri(self):
        if self.type not in self.MEDIA_KIND_BY_TYPE:
            return ''
        if self.type == MessageTypeChoice.FILE and not self.content.lstrip().startswith('{'):
            return ''
        payload = self._parse_payload(self.content)
        return (payload.get('uri') or '').strip()

    def source_media_key(self):
        source_uri = self.source_media_uri()
        return urlparse(source_uri).path.lstrip('/') if source_uri else ''

    def jsonl(self, request: HttpRequest = None, include_deleted: bool = False):
        viewer = self._viewer_from_request(request)
        content = self.system_message_text(viewer) if self.type == MessageTypeChoice.SYSTEM else self.preview_text()
        payload = dict(
            message_id=self.id,
            client_message_id=self.client_message_id,
            user=self.user.tiny_json(),
            type=self.type,
            content=content,
            payload=self._payload_for_type(request=request),
            reply_to=self._reply_to_payload(request=request),
            mentions=[mention.user.tiny_json() for mention in self.chat_mentions.all()],
            created_at=self.created_at.timestamp(),
        )
        if include_deleted:
            payload['is_deleted'] = self.is_deleted
        return payload

    @classmethod
    def index(cls, message_id):
        try:
            return cls.objects.get(id=message_id, is_deleted=False)
        except cls.DoesNotExist:
            raise MessageErrors.NOT_EXISTS

    @classmethod
    def latest(cls, chat: Chat, limit: int, request: HttpRequest = None, user: User = None):
        queryset = cls.visible_for_user(chat, user) if user is not None else cls.visible_in_chat(chat)
        messages = queryset.select_related('user', 'reply_to', 'reply_to__user', 'media_asset', 'forward_bundle').prefetch_related('chat_mentions__user', 'forward_bundle__items__media_asset').order_by('-id')[:limit]
        return [message.jsonl(request=request) for message in messages]

    @classmethod
    def older(cls, chat: Chat, message_id, limit: int, request: HttpRequest = None, user: User = None):
        queryset = cls.visible_for_user(chat, user) if user is not None else cls.visible_in_chat(chat)
        messages = queryset.select_related('user', 'reply_to', 'reply_to__user', 'media_asset', 'forward_bundle').prefetch_related('chat_mentions__user', 'forward_bundle__items__media_asset').filter(id__lt=message_id).order_by('-id')[:limit]
        return [message.jsonl(request=request) for message in messages]

    @classmethod
    def newer(cls, chat: Chat, message_id, limit: int, request: HttpRequest = None, user: User = None):
        queryset = cls.visible_for_user(chat, user) if user is not None else cls.visible_in_chat(chat)
        messages = queryset.select_related('user', 'reply_to', 'reply_to__user', 'media_asset', 'forward_bundle').prefetch_related('chat_mentions__user', 'forward_bundle__items__media_asset').filter(id__gt=message_id).order_by('id')[:limit]
        return [message.jsonl(request=request) for message in messages]

    @classmethod
    def search(cls, chat: Chat, user: User, keyword=None, message_type=None, before=None, limit=30, request=None):
        queryset = cls.visible_for_user(chat, user).select_related('user', 'reply_to', 'reply_to__user', 'media_asset', 'forward_bundle').prefetch_related('chat_mentions__user', 'forward_bundle__items__media_asset')
        normalized_keyword = (keyword or '').strip()
        if normalized_keyword:
            queryset = queryset.filter(content__icontains=normalized_keyword)
        if message_type is not None:
            queryset = queryset.filter(type=message_type)
        if before is not None:
            queryset = queryset.filter(id__lt=before)
        messages = list(queryset.order_by('-id')[:limit + 1])
        has_more = len(messages) > limit
        messages = messages[:limit]
        return dict(
            items=[message.jsonl(request=request) for message in messages],
            has_more=has_more,
            next_before=messages[-1].id if has_more and messages else None,
        )

    def remove(self):
        if self.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        if self.is_deleted:
            return
        self.is_deleted = True
        self.save(update_fields=['is_deleted'])
        MessageEvent.record_recalled(self)

    def hide_for(self, user: User):
        if self.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        state, created = MessageUserState.objects.get_or_create(message=self, user=user)
        if created:
            MessageEvent.record_hidden(self, user)
        return state

    @classmethod
    def clear_for_user(cls, chat: Chat, user: User):
        if not chat.has_active_member(user):
            raise MessageErrors.NOT_A_MEMBER
        messages = list(
            cls.visible_for_user(chat, user)
            .select_for_update()
            .only('id', 'chat_id')
        )
        if not messages:
            return 0
        hidden_at = timezone.now()
        MessageUserState.objects.bulk_create(
            [MessageUserState(message=message, user=user, hidden_at=hidden_at) for message in messages],
            ignore_conflicts=True,
        )
        MessageEvent.objects.bulk_create([
            MessageEvent(
                message=message,
                chat=chat,
                actor=user,
                target_user=user,
                type=MessageEventTypeChoice.HIDDEN,
                created_at=hidden_at,
            )
            for message in messages
        ])
        return len(messages)


class MessageUserState(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='hidden_states')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hidden_message_states')
    hidden_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['message', 'user'], name='message_user_hidden_unique'),
        ]


class MessageHistoryRecovery(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='message_history_recoveries')
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='message_history_recoveries')
    restored_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @classmethod
    def allowance_for(cls, user):
        if not user.verified:
            return 0
        return 6 if user.is_permanent_vip else 1

    @classmethod
    def status_for(cls, chat, user):
        limit = cls.allowance_for(user)
        used = cls.objects.filter(user=user).count()
        hidden_count = MessageUserState.objects.filter(
            user=user,
            message__chat=chat,
            message__is_deleted=False,
        ).count()
        return dict(
            eligible=limit > 0,
            has_password=user.has_password,
            limit=limit,
            used=used,
            remaining=max(0, limit - used),
            hidden_count=hidden_count,
            can_restore=limit > used and hidden_count > 0 and user.has_password,
        )

    @classmethod
    def restore(cls, chat, user, password):
        if not chat.has_active_member(user):
            raise MessageErrors.NOT_A_MEMBER
        with transaction.atomic():
            locked_user = User.objects.select_for_update().get(id=user.id)
            if not locked_user.has_password:
                raise UserErrors.PASSWORD_NOT_SET
            if not function.verify_password(password, locked_user.salt, locked_user.password):
                raise UserErrors.PASSWORD_ERROR
            status = cls.status_for(chat, locked_user)
            if not status['eligible']:
                raise MessageErrors.HISTORY_RECOVERY_VERIFICATION_REQUIRED
            if status['remaining'] <= 0:
                raise MessageErrors.HISTORY_RECOVERY_LIMIT_REACHED
            states = list(
                MessageUserState.objects.select_for_update()
                .select_related('message')
                .filter(user=user, message__chat=chat, message__is_deleted=False)
            )
            if not states:
                raise MessageErrors.HISTORY_RECOVERY_EMPTY
            restored_at = timezone.now()
            MessageEvent.objects.bulk_create([
                MessageEvent(
                    message=state.message,
                    chat=chat,
                    actor=user,
                    target_user=user,
                    type=MessageEventTypeChoice.RESTORED,
                    created_at=restored_at,
                )
                for state in states
            ])
            restored_count = len(states)
            MessageUserState.objects.filter(id__in=[state.id for state in states]).delete()
            cls.objects.create(user=locked_user, chat=chat, restored_count=restored_count)
        result = cls.status_for(chat, user)
        result['restored_count'] = restored_count
        return result


class MessageEvent(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='sync_events')
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='message_events')
    actor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='message_events')
    target_user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name='targeted_message_events')
    type = models.IntegerField(choices=MessageEventTypeChoice.to_choices())
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    @classmethod
    def record_created(cls, message):
        return cls.objects.create(message=message, chat=message.chat, actor=message.user, type=MessageEventTypeChoice.CREATED)

    @classmethod
    def record_hidden(cls, message, user):
        return cls.objects.create(message=message, chat=message.chat, actor=user, target_user=user, type=MessageEventTypeChoice.HIDDEN)

    @classmethod
    def record_recalled(cls, message):
        return cls.objects.create(message=message, chat=message.chat, actor=message.user, type=MessageEventTypeChoice.RECALLED)

    @classmethod
    def sync_for_user(cls, user: User, after: int, limit: int, request: HttpRequest = None):
        from Chat.models import ChatReadState, ChatUserPreference

        chat_ids = [chat.id for chat in Chat.get_user_chats(user)]
        rows = list(
            cls.objects.select_related('chat', 'message', 'message__user', 'message__reply_to', 'message__reply_to__user')
            .prefetch_related('message__chat_mentions__user')
            .filter(id__gt=after)
            .filter(Q(target_user=user) | Q(target_user__isnull=True, chat_id__in=chat_ids))
            .order_by('id')[:limit + 1]
        )
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = []
        names = {
            MessageEventTypeChoice.CREATED: 'message.created',
            MessageEventTypeChoice.HIDDEN: 'message.hidden',
            MessageEventTypeChoice.RECALLED: 'message.recalled',
            MessageEventTypeChoice.RESTORED: 'message.restored',
        }
        visible_created_message_ids = set()
        created_rows_by_chat = {}
        for row in rows:
            if row.type in (MessageEventTypeChoice.CREATED, MessageEventTypeChoice.RESTORED):
                created_rows_by_chat.setdefault(row.chat_id, []).append(row.message_id)
        for chat_id, message_ids in created_rows_by_chat.items():
            chat = next(row.chat for row in rows if row.chat_id == chat_id)
            visible_created_message_ids.update(
                Message.visible_for_user(chat, user).filter(id__in=message_ids).values_list('id', flat=True)
            )

        for row in rows:
            event = dict(event_id=row.id, type=names[row.type], chat_id=row.chat_id, message_id=row.message_id)
            if row.type in (MessageEventTypeChoice.CREATED, MessageEventTypeChoice.RESTORED) and not row.message.is_deleted:
                if row.message_id in visible_created_message_ids and not MessageUserState.objects.filter(message=row.message, user=user).exists():
                    event['message'] = row.message.jsonl(request=request)
                    event['message']['mentioned_me'] = row.message.chat_mentions.filter(user=user).exists()
            events.append(event)

        affected_chats = {row.chat_id: row.chat for row in rows}
        muted_badge_chat_ids = set(
            ChatUserPreference.objects.filter(
                user=user,
                chat_id__in=affected_chats,
                unread_badge_muted=True,
            ).values_list('chat_id', flat=True)
        )
        read_states = {
            state.chat_id: state.last_read_at
            for state in ChatReadState.objects.filter(user=user, chat_id__in=affected_chats)
        }
        chat_states = [
            dict(
                chat_id=chat_id,
                unread_count=ChatReadState.unread_count(chat, user),
                unread_badge_muted=chat_id in muted_badge_chat_ids,
                has_unread_mention=ChatReadState.has_unread_mention(chat, user) if chat.group else False,
                last_read_at=read_states[chat_id].timestamp() if read_states.get(chat_id) else None,
            )
            for chat_id, chat in affected_chats.items()
        ]
        return dict(
            events=events,
            chat_states=chat_states,
            has_more=has_more,
            next_after=rows[-1].id if rows else after,
        )


class PinnedMessage(models.Model):
    MAX_PER_CHAT = 20

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='pinned_messages')
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='pins')
    pinned_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pinned_messages')
    pinned_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-pinned_at']
        constraints = [
            models.UniqueConstraint(fields=['message', 'pinned_by'], name='unique_message_pin_user'),
        ]

    @classmethod
    def require_manage_permission(cls, chat, user):
        if not chat.has_active_member(user):
            raise MessageErrors.NOT_A_MEMBER
        if chat.group and not chat.is_owner(user):
            raise MessageErrors.PIN_FORBIDDEN

    @classmethod
    def pin(cls, message, user):
        if message.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        cls.require_manage_permission(message.chat, user)
        existing = cls.objects.filter(message=message, pinned_by=user).first()
        if existing is not None:
            return existing
        pinned_message_count = cls.objects.filter(
            chat=message.chat,
            message__is_deleted=False,
        ).values('message_id').distinct().count()
        if pinned_message_count >= cls.MAX_PER_CHAT and not cls.objects.filter(message=message).exists():
            raise MessageErrors.PIN_LIMIT_REACHED
        pin, created = cls.objects.get_or_create(
            message=message,
            pinned_by=user,
            defaults={'chat': message.chat},
        )
        if created:
            user.award_growth('explore:pin_message')
            Message.create_system(
                message.chat,
                user,
                'message_pinned',
                target_message_id=message.id,
            )
        return pin

    @classmethod
    def unpin(cls, message, user):
        if message.type == MessageTypeChoice.SYSTEM:
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        cls.require_manage_permission(message.chat, user)
        deleted, _details = cls.objects.filter(message=message, pinned_by=user).delete()
        if deleted:
            Message.create_system(
                message.chat,
                user,
                'message_unpinned',
                target_message_id=message.id,
            )

    @classmethod
    def list_for_chat(cls, chat, user=None):
        rows = cls.objects.filter(
            chat=chat,
            message__is_deleted=False,
        ).select_related('message', 'message__user', 'pinned_by').order_by('-pinned_at')
        if user is not None:
            visible_message_ids = Message.visible_for_user(chat, user).values_list('id', flat=True)
            rows = rows.filter(message_id__in=visible_message_ids)
        return cls.aggregate_rows(rows, limit=cls.MAX_PER_CHAT)

    @classmethod
    def aggregate_for_message(cls, message):
        rows = cls.objects.filter(
            message=message,
            message__is_deleted=False,
        ).select_related('message', 'message__user', 'pinned_by').order_by('-pinned_at')
        aggregated = cls.aggregate_rows(rows, limit=1)
        return aggregated[0] if aggregated else None

    @classmethod
    def aggregate_rows(cls, rows, limit):
        grouped = {}
        for pin in rows:
            if pin.message_id not in grouped:
                if len(grouped) >= limit:
                    continue
                grouped[pin.message_id] = dict(
                    pin_id=pin.id,
                    message=pin.message,
                    pinned_by_users=[],
                    pinned_at=pin.pinned_at,
                )
            grouped[pin.message_id]['pinned_by_users'].append(pin.pinned_by)
        return list(grouped.values())

    @classmethod
    def aggregate_json(cls, payload, request=None):
        return dict(
            pin_id=payload['pin_id'],
            message=payload['message'].jsonl(request=request),
            pinned_by_users=[user.tiny_json() for user in payload['pinned_by_users']],
            pinned_at=payload['pinned_at'].timestamp(),
        )


class MediaAsset(models.Model):
    KIND_IMAGE = 0
    KIND_VIDEO = 1
    KIND_AUDIO = 2
    KIND_FILE = 3
    STATUS_PENDING = 0
    STATUS_READY = 1
    STATUS_FAILED = 2
    GEOCODING_PENDING = 0
    GEOCODING_READY = 1
    GEOCODING_FAILED = 2
    GEOCODING_UNAVAILABLE = 3
    _FETCHING_IDS = set()
    _FETCHING_LOCK = threading.Lock()

    source_key = models.CharField(max_length=255, unique=True)
    source_uri = models.CharField(max_length=500)
    blob_slug = models.CharField(max_length=32, unique=True, db_index=True, default=generate_media_blob_slug)
    kind = models.IntegerField(db_index=True)
    mime_type = models.CharField(max_length=100, blank=True, default='')
    file_name = models.CharField(max_length=180, blank=True, default='')
    status = models.IntegerField(default=STATUS_PENDING, db_index=True)
    raw_metadata = models.JSONField(default=dict, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    file_size = models.BigIntegerField(null=True, blank=True)
    pixel_width = models.PositiveIntegerField(null=True, blank=True)
    pixel_height = models.PositiveIntegerField(null=True, blank=True)
    frame_rate = models.FloatField(null=True, blank=True)
    bit_rate = models.BigIntegerField(null=True, blank=True)
    video_codec = models.CharField(max_length=64, blank=True, default='')
    audio_codec = models.CharField(max_length=64, blank=True, default='')
    make = models.CharField(max_length=255, blank=True, default='')
    model = models.CharField(max_length=255, blank=True, default='')
    lens_model = models.CharField(max_length=255, blank=True, default='')
    software = models.CharField(max_length=255, blank=True, default='')
    taken_at = models.DateTimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    address = models.CharField(max_length=500, blank=True, default='')
    geocoding_provider = models.CharField(max_length=32, blank=True, default='')
    geocoding_status = models.IntegerField(default=GEOCODING_PENDING, db_index=True)
    geocoding_error = models.CharField(max_length=500, blank=True, default='')
    error = models.CharField(max_length=500, blank=True, default='')
    detail_metadata_checked_at = models.DateTimeField(null=True, blank=True)
    detail_metadata_error = models.CharField(max_length=500, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def kind_for_name(cls, kind):
        return {'image': cls.KIND_IMAGE, 'video': cls.KIND_VIDEO, 'audio': cls.KIND_AUDIO, 'file': cls.KIND_FILE}[kind]

    @classmethod
    def queue(cls, source_key, source_uri, kind, mime_type=None, duration_seconds=None, file_size=None, file_name=None):
        normalized_uri = str(source_uri or '').strip()
        normalized_key = urlparse(str(source_key or '').strip()).path.lstrip('/')
        if not normalized_key:
            normalized_key = urlparse(normalized_uri).path.lstrip('/')
        if not normalized_key or not normalized_uri:
            raise ValueError('media source key and URI are required')
        try:
            metadata, _created = cls.objects.get_or_create(
                source_key=normalized_key,
                defaults=dict(
                    source_uri=normalized_uri, kind=kind,
                    mime_type=str(mime_type or '')[:100], file_name=str(file_name or '')[:180],
                    duration_seconds=duration_seconds, file_size=file_size,
                    status=cls.STATUS_PENDING if kind in {cls.KIND_IMAGE, cls.KIND_VIDEO} else cls.STATUS_READY,
                    geocoding_status=cls.GEOCODING_PENDING if kind in {cls.KIND_IMAGE, cls.KIND_VIDEO} else cls.GEOCODING_UNAVAILABLE,
                ),
            )
        except IntegrityError:
            metadata = cls.objects.get(source_key=normalized_key)
        updates = []
        if metadata.source_uri != normalized_uri:
            metadata.source_uri = normalized_uri
            updates.append('source_uri')
        if metadata.kind != kind:
            metadata.kind = kind
            updates.append('kind')
        for field, value in (
            ('mime_type', str(mime_type or '')[:100]), ('file_name', str(file_name or '')[:180]),
            ('duration_seconds', duration_seconds), ('file_size', file_size),
        ):
            if value not in (None, '') and getattr(metadata, field) != value:
                setattr(metadata, field, value)
                updates.append(field)
        if updates:
            metadata.save(update_fields=[*updates, 'updated_at'])
        if kind in {cls.KIND_IMAGE, cls.KIND_VIDEO}:
            transaction.on_commit(lambda: cls.fetch_async(metadata.id))
        return metadata

    @classmethod
    def fetch_async(cls, metadata_id):
        with cls._FETCHING_LOCK:
            if metadata_id in cls._FETCHING_IDS:
                return
            cls._FETCHING_IDS.add(metadata_id)
        threading.Thread(target=cls.refresh_by_id, args=(metadata_id,), daemon=True).start()

    @classmethod
    def refresh_by_id(cls, metadata_id):
        close_old_connections()
        try:
            metadata = cls.objects.get(id=metadata_id)
            cls.refresh(metadata)
        finally:
            with cls._FETCHING_LOCK:
                cls._FETCHING_IDS.discard(metadata_id)
            close_old_connections()

    @classmethod
    def refresh(cls, metadata, geocode=True):
        from Message.image_metadata import fetch_qiniu_exif, fetch_qiniu_image_info, parse_exif, parse_image_info
        from Message.video_metadata import fetch_qiniu_avinfo, parse_avinfo

        previous_coordinates = (metadata.latitude, metadata.longitude)
        previous_address = metadata.address
        previous_provider = metadata.geocoding_provider
        detail_metadata_error = ''
        try:
            if metadata.kind == cls.KIND_IMAGE:
                properties = parse_image_info(fetch_qiniu_image_info(metadata.source_uri))
                try:
                    raw_metadata = fetch_qiniu_exif(metadata.source_uri)
                except Exception as error:
                    raw_metadata = {}
                    detail_metadata_error = str(error)[:500]
                properties.update(parse_exif(raw_metadata))
            elif metadata.kind == cls.KIND_VIDEO:
                raw_metadata = fetch_qiniu_avinfo(metadata.source_uri)
                properties = parse_avinfo(raw_metadata)
            else:
                raise ValueError('unsupported media metadata kind')
            for field, value in properties.items():
                setattr(metadata, field, value)
            metadata.raw_metadata = raw_metadata
            metadata.detail_metadata_checked_at = timezone.now()
            metadata.detail_metadata_error = detail_metadata_error
            metadata.status = cls.STATUS_READY
            metadata.error = ''
            coordinates = (metadata.latitude, metadata.longitude)
            has_coordinates = all(value is not None for value in coordinates)
            if has_coordinates and coordinates == previous_coordinates and previous_address:
                metadata.address = previous_address
                metadata.geocoding_provider = previous_provider
                metadata.geocoding_status = cls.GEOCODING_READY
            elif has_coordinates:
                metadata.address = ''
                metadata.geocoding_provider = ''
                metadata.geocoding_status = cls.GEOCODING_PENDING
            else:
                metadata.address = ''
                metadata.geocoding_provider = ''
                metadata.geocoding_status = cls.GEOCODING_UNAVAILABLE
            metadata.geocoding_error = ''
            metadata.save()
        except Exception as error:
            metadata.status = cls.STATUS_FAILED
            metadata.error = str(error)[:500]
            metadata.save(update_fields=['status', 'error', 'updated_at'])
            return metadata

        if geocode and metadata.geocoding_status == cls.GEOCODING_PENDING:
            cls.refresh_geocoding(metadata)
        return metadata

    @classmethod
    def refresh_geocoding(cls, metadata):
        from Message.image_metadata import reverse_geocode

        if metadata.latitude is None or metadata.longitude is None:
            metadata.geocoding_status = cls.GEOCODING_UNAVAILABLE
            metadata.geocoding_error = ''
        else:
            try:
                cached = cls.objects.filter(
                    latitude=metadata.latitude,
                    longitude=metadata.longitude,
                ).exclude(id=metadata.id).exclude(address='').values('address', 'geocoding_provider').first()
                if cached:
                    metadata.address = cached['address']
                    metadata.geocoding_provider = cached['geocoding_provider']
                else:
                    metadata.address, metadata.geocoding_provider = reverse_geocode(
                        metadata.latitude,
                        metadata.longitude,
                    )
                metadata.geocoding_status = cls.GEOCODING_READY
                metadata.geocoding_error = ''
            except Exception as error:
                metadata.geocoding_status = cls.GEOCODING_FAILED
                metadata.geocoding_error = str(error)[:500]
        metadata.save(update_fields=[
            'address', 'geocoding_provider', 'geocoding_status', 'geocoding_error', 'updated_at',
        ])
        return metadata

    def jsonl(self):
        return dict(
            status=self.status,
            duration_seconds=self.duration_seconds,
            file_size=self.file_size,
            pixel_width=self.pixel_width,
            pixel_height=self.pixel_height,
            frame_rate=self.frame_rate,
            bit_rate=self.bit_rate,
            video_codec=self.video_codec,
            audio_codec=self.audio_codec,
            make=self.make,
            model=self.model,
            lens_model=self.lens_model,
            software=self.software,
            taken_at=self.taken_at.timestamp() if self.taken_at else None,
            latitude=self.latitude,
            longitude=self.longitude,
            address=self.address,
            geocoding_provider=self.geocoding_provider,
            geocoding_status=self.geocoding_status,
            detail_metadata_checked_at=self.detail_metadata_checked_at.timestamp() if self.detail_metadata_checked_at else None,
            detail_metadata_error=self.detail_metadata_error,
        )


class ForwardBundle(models.Model):
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='forward_bundles')
    source_chat = models.ForeignKey(Chat, on_delete=models.SET_NULL, null=True, blank=True, related_name='forward_bundles')
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def create_from_messages(cls, messages, user, request=None):
        ordered = sorted(messages, key=lambda message: (message.created_at, message.id))
        source_chat = ordered[0].chat if ordered else None
        bundle = cls.objects.create(created_by=user, source_chat=source_chat)
        ForwardBundleItem.objects.bulk_create([
            ForwardBundleItem(
                bundle=bundle,
                position=position,
                original_message=message,
                media_asset=message.media_asset,
                message_type=message.type,
                author=message.user.tiny_json(),
                content=message.preview_text(),
                payload=message._payload_for_type(request=request) or {},
                sent_at=message.created_at,
            )
            for position, message in enumerate(ordered)
        ])
        return bundle

    def jsonl(self, request=None):
        prefetched = getattr(self, '_prefetched_objects_cache', {}).get('items')
        bundle_items = prefetched if prefetched is not None else self.items.select_related('media_asset').order_by('position')
        items = [item.jsonl(request=request) for item in bundle_items]
        authors = []
        for item in items:
            name = str((item.get('author') or {}).get('name') or '').strip()
            if name and name not in authors:
                authors.append(name)
        return dict(
            kind='forward_bundle',
            bundle_id=self.id,
            title=_('Chat history'),
            summary=', '.join(authors[:3]),
            item_count=len(items),
            items=items,
        )


class ForwardBundleItem(models.Model):
    bundle = models.ForeignKey(ForwardBundle, on_delete=models.CASCADE, related_name='items')
    position = models.PositiveIntegerField(default=0)
    original_message = models.ForeignKey(
        Message, on_delete=models.SET_NULL, null=True, blank=True, related_name='forward_snapshots',
    )
    media_asset = models.ForeignKey(
        MediaAsset, on_delete=models.PROTECT, null=True, blank=True, related_name='forward_items',
    )
    message_type = models.IntegerField()
    author = models.JSONField(default=dict)
    content = models.CharField(max_length=512, blank=True, default='')
    payload = models.JSONField(default=dict)
    sent_at = models.DateTimeField()

    class Meta:
        ordering = ['position']
        constraints = [
            models.UniqueConstraint(fields=['bundle', 'position'], name='forward_bundle_unique_position'),
        ]

    def jsonl(self, request=None):
        payload = dict(self.payload or {})
        if self.media_asset_id:
            path = reverse('message blob', kwargs={'blob_slug': self.media_asset.blob_slug})
            payload['uri'] = request.build_absolute_uri(path) if request is not None else path
            if self.message_type in (MessageTypeChoice.IMAGE, MessageTypeChoice.VIDEO):
                thumbnail_path = reverse('message blob thumbnail', kwargs={'blob_slug': self.media_asset.blob_slug})
                payload['thumbnail_uri'] = request.build_absolute_uri(thumbnail_path) if request is not None else thumbnail_path
            if self.message_type == MessageTypeChoice.IMAGE:
                payload['image_metadata'] = self.media_asset.jsonl()
            elif self.message_type == MessageTypeChoice.VIDEO:
                payload['video_metadata'] = self.media_asset.jsonl()
        return dict(
            position=self.position,
            type=self.message_type,
            author=self.author,
            content=self.content,
            payload=payload,
            sent_at=self.sent_at.timestamp(),
        )


class MediaAssetAlias(models.Model):
    slug = models.CharField(max_length=32, unique=True, db_index=True)
    asset = models.ForeignKey(MediaAsset, on_delete=models.CASCADE, related_name='aliases')

    @classmethod
    def resolve(cls, slug):
        normalized = str(slug or '').strip().lower()
        asset = MediaAsset.objects.filter(blob_slug=normalized).first()
        if asset is not None:
            return asset
        alias = cls.objects.select_related('asset').filter(slug=normalized).first()
        return alias.asset if alias else None
