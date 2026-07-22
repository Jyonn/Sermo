import hashlib
import json
import ipaddress
import os
import re
import socket
import threading
import uuid
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from django.db import close_old_connections, transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from smartdjango import models, Choice

from Chat.models import Chat
from Message.validators import MessageErrors, MessageValidator
from User.models import User
from utils.qiniu import sign_private_download_url, avatar_uri_for_key, build_message_image_thumbnail_uri, validate_message_media_key


class MessageTypeChoice(Choice):
    TEXT = 0
    IMAGE = 1
    FILE = 2
    SYSTEM = 3
    VIDEO = 4
    AUDIO = 5


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
                self.icons.append(href)

    def handle_endtag(self, tag):
        if tag.lower() == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self):
        return ' '.join(''.join(self.title_parts).split())


class LinkPreview(models.Model):
    URL_RE = re.compile(r'https?://[^\s<>"\'，。！？、；：）】》]+', re.IGNORECASE)
    HTTP_CHARSET_RE = re.compile(r'charset=["\']?([^;"\']+)', re.IGNORECASE)
    HTML_CHARSET_RE = re.compile(br'<meta[^>]+charset=["\']?\s*([a-zA-Z0-9._-]+)', re.IGNORECASE)
    TRAILING_PUNCTUATION = '.,;:!?)]}，。！？、；：）】》'
    MOJIBAKE_MARKERS = ('ï¼', 'ï½', 'ã€', 'Ã', 'Â')
    RETRYABLE_ERROR_MARKERS = ('already consumed',)
    USER_AGENT = 'SermoLinkPreviewBot/1.0'
    MAX_HTML_BYTES = 256 * 1024
    MAX_REDIRECTS = 3
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
    def fetch_preview_data(cls, url: str):
        current_url = cls.normalize_public_url(url)
        if not current_url:
            raise ValueError('invalid url')

        response = None
        for _ in range(cls.MAX_REDIRECTS + 1):
            cls.normalize_public_url(current_url)
            response = requests.get(
                current_url,
                headers={'User-Agent': cls.USER_AGENT, 'Accept': 'text/html,application/xhtml+xml'},
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
        image_url = parser.meta.get('og:image') or parser.meta.get('twitter:image') or ''
        favicon_url = parser.icons[0] if parser.icons else ''
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
        if created or preview.status == LinkPreviewStatusChoice.PENDING:
            transaction.on_commit(lambda: cls.fetch_async(preview.id))
        return preview

    @classmethod
    def fetch_async(cls, preview_id: int):
        with cls._FETCHING_LOCK:
            if preview_id in cls._FETCHING_IDS:
                return
            cls._FETCHING_IDS.add(preview_id)
        thread = threading.Thread(target=cls.fetch_and_update, args=(preview_id,), daemon=True)
        thread.start()

    @classmethod
    def fetch_and_update(cls, preview_id: int):
        close_old_connections()
        try:
            preview = cls.objects.get(id=preview_id)
            if preview.status == LinkPreviewStatusChoice.READY:
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
    }

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    type = models.IntegerField(choices=MessageTypeChoice.to_choices())
    content = models.CharField(max_length=vldt.MAX_CONTENT_LENGTH)
    blob_slug = models.CharField(max_length=32, null=True, blank=True, unique=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def visible_queryset(cls):
        return cls.objects.filter(is_deleted=False)

    @classmethod
    def visible_in_chat(cls, chat: Chat):
        return cls.visible_queryset().filter(chat=chat)

    @classmethod
    def create(cls, chat: Chat, user: User, message_type, content):
        if chat.has_active_member(user):
            normalized_content = cls.normalize_content(message_type, content)
            message = cls.objects.create(chat=chat, user=user, type=message_type, content=normalized_content)
            if message.type in cls.MEDIA_KIND_BY_TYPE:
                message.ensure_blob_slug(save=True)
            if message.type == MessageTypeChoice.TEXT:
                LinkPreview.queue_for_text(message.content)
            return message
        raise MessageErrors.NOT_A_MEMBER

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

        raise MessageErrors.TYPE_INVALID

    @classmethod
    def _generate_blob_slug(cls):
        return uuid.uuid4().hex

    def ensure_blob_slug(self, save: bool = False):
        if self.type not in self.MEDIA_KIND_BY_TYPE:
            return None
        if self.blob_slug:
            return self.blob_slug

        blob_slug = self._generate_blob_slug()
        while Message.objects.filter(blob_slug=blob_slug).exists():
            blob_slug = self._generate_blob_slug()
        self.blob_slug = blob_slug
        if save:
            self.save(update_fields=['blob_slug'])
        return self.blob_slug

    def _blob_path(self, thumbnail: bool = False):
        self.ensure_blob_slug(save=False)
        if not self.blob_slug:
            return ''
        route_name = 'message blob thumbnail' if thumbnail else 'message blob'
        return reverse(route_name, kwargs={'blob_slug': self.blob_slug})

    def media_blob_uri(self, request: HttpRequest = None, thumbnail: bool = False):
        path = self._blob_path(thumbnail=thumbnail)
        if not path:
            return ''
        if request is None:
            return path
        return request.build_absolute_uri(path)

    def _payload_for_type(self, request: HttpRequest = None):
        if self.type == MessageTypeChoice.TEXT:
            payload = dict(kind='text', text=self.content)
            link_preview = LinkPreview.queue_for_text(self.content)
            if link_preview is not None:
                payload['link_preview'] = link_preview.jsonl()
            return payload
        if self.type == MessageTypeChoice.SYSTEM:
            return dict(kind='system', text=self.content)
        if self.type == MessageTypeChoice.FILE and not self.content.lstrip().startswith('{'):
            return dict(kind='file', text=self.content)
        if self.type in self.MEDIA_KIND_BY_TYPE:
            payload = self._parse_payload(self.content)
            uri = (payload.get('uri') or '').strip()
            response = dict(kind=payload.get('kind') or self.MEDIA_KIND_BY_TYPE[self.type])
            if self.blob_slug:
                response['uri'] = self.media_blob_uri(request=request)
                if self.type == MessageTypeChoice.IMAGE:
                    response['thumbnail_uri'] = self.media_blob_uri(request=request, thumbnail=True)
            elif uri:
                response['uri'] = sign_private_download_url(uri)
                if self.type == MessageTypeChoice.IMAGE:
                    response['thumbnail_uri'] = build_message_image_thumbnail_uri(uri)
            mime_type = (str(payload.get('mime_type') or '').strip())[:100]
            if mime_type:
                response['mime_type'] = mime_type
            if self.type == MessageTypeChoice.AUDIO and 'duration_seconds' in payload:
                response['duration_seconds'] = payload.get('duration_seconds')
            if self.type == MessageTypeChoice.FILE:
                response['file_name'] = payload.get('file_name') or '文件'
                response['file_size'] = payload.get('file_size') or 0
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

    def source_media_uri(self):
        if self.type not in self.MEDIA_KIND_BY_TYPE:
            return ''
        if self.type == MessageTypeChoice.FILE and not self.content.lstrip().startswith('{'):
            return ''
        payload = self._parse_payload(self.content)
        return (payload.get('uri') or '').strip()

    def jsonl(self, request: HttpRequest = None):
        return dict(
            message_id=self.id,
            user=self.user.tiny_json(),
            type=self.type,
            content=self.preview_text(),
            payload=self._payload_for_type(request=request),
            created_at=self.created_at.timestamp(),
        )

    @classmethod
    def index(cls, message_id):
        try:
            return cls.objects.get(id=message_id, is_deleted=False)
        except cls.DoesNotExist:
            raise MessageErrors.NOT_EXISTS

    @classmethod
    def index_by_blob_slug(cls, blob_slug):
        normalized = (blob_slug or '').strip().lower()
        try:
            return cls.objects.get(blob_slug=normalized, is_deleted=False)
        except cls.DoesNotExist:
            raise MessageErrors.NOT_EXISTS

    @classmethod
    def latest(cls, chat: Chat, limit: int, request: HttpRequest = None):
        messages = cls.visible_in_chat(chat).order_by('-created_at')[:limit]
        return [message.jsonl(request=request) for message in messages]

    @classmethod
    def older(cls, chat: Chat, message_id, limit: int, request: HttpRequest = None):
        messages = cls.visible_in_chat(chat).filter(id__lt=message_id).order_by('-created_at')[:limit]
        return [message.jsonl(request=request) for message in messages]

    @classmethod
    def newer(cls, chat: Chat, message_id, limit: int, request: HttpRequest = None):
        messages = cls.visible_in_chat(chat).filter(id__gt=message_id).order_by('created_at')[:limit]
        return [message.jsonl(request=request) for message in messages]

    @classmethod
    def sync_for_user(cls, user: User, after: int, limit: int, request: HttpRequest = None):
        from Chat.models import Chat

        chats = Chat.get_user_chats(user)
        chat_ids = [chat.id for chat in chats]
        if not chat_ids:
            return dict(items=[], has_more=False, next_after=after)

        rows = list(
            cls.visible_queryset()
            .filter(chat_id__in=chat_ids, id__gt=after)
            .order_by('id')[:limit + 1]
        )
        has_more = len(rows) > limit
        rows = rows[:limit]

        items = []
        for message in rows:
            payload = message.jsonl(request=request)
            payload['chat_id'] = message.chat_id
            items.append(payload)

        next_after = after
        if rows:
            next_after = rows[-1].id

        return dict(
            items=items,
            has_more=has_more,
            next_after=next_after,
        )

    def remove(self):
        self.is_deleted = True
        self.save(update_fields=['is_deleted'])
