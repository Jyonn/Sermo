import json
import uuid

from django.http import HttpRequest
from django.urls import reverse

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


class Message(models.Model):
    validators = MessageValidator
    vldt = MessageValidator
    MEDIA_KIND_BY_TYPE = {
        MessageTypeChoice.IMAGE: 'image',
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
        return json.dumps(normalized, separators=(',', ':'), ensure_ascii=False)

    @classmethod
    def normalize_content(cls, message_type, content):
        if message_type in (MessageTypeChoice.TEXT, MessageTypeChoice.SYSTEM, MessageTypeChoice.FILE):
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
            return dict(kind='text', text=self.content)
        if self.type == MessageTypeChoice.SYSTEM:
            return dict(kind='system', text=self.content)
        if self.type == MessageTypeChoice.FILE:
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
