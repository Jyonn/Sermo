from diq import Dictify
from django.db import models
from smartdjango import Choice

from Chat.models import BaseChat, GroupChat, SingleChat
from Message.validators import MessageErrors, MessageValidator
from User.models import BaseUser


class MessageTypeChoice(Choice):
    TEXT = 0
    IMAGE = 1
    FILE = 2
    SYSTEM = 3


class Message(models.Model, Dictify):
    vldt = MessageValidator

    chat = models.ForeignKey(BaseChat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(BaseUser, on_delete=models.CASCADE)

    type = models.IntegerField(choices=MessageTypeChoice.to_choices())
    content = models.CharField(max_length=vldt.MAX_CONTENT_LENGTH)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def create(cls, chat: BaseChat, user: BaseUser, message_type, content):
        if user == chat.host:
            return cls.objects.create(chat=chat, user=user, type=message_type, content=content)
        if isinstance(chat, GroupChat) and user in chat.guests.all():
            return cls.objects.create(chat=chat, user=user, type=message_type, content=content)
        if isinstance(chat, SingleChat) and user == chat.guest:
            return cls.objects.create(chat=chat, user=user, type=message_type, content=content)
        raise MessageErrors.NOT_A_MEMBER

    def _dictify_user(self):
        return self.user.specify().tiny_json()

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def jsonl(self):
        return self.dictify('id->message_id', 'user', 'type', 'content', 'created_at')

    @classmethod
    def index(cls, message_id):
        messages = cls.objects.filter(id=message_id, is_deleted=False)
        if not messages.exists():
            raise MessageErrors.NOT_EXISTS
        return messages.first()

    @classmethod
    def latest(cls, chat: BaseChat, limit: int):
        messages = cls.objects.filter(chat=chat, is_deleted=False).order_by('-created_at')[:limit]
        return [message.jsonl() for message in messages]

    @classmethod
    def older(cls, chat: BaseChat, message_id, limit: int):
        messages = cls.objects.filter(chat=chat, id__lt=message_id, is_deleted=False).order_by('-created_at')[:limit]
        return [message.jsonl() for message in messages]

    @classmethod
    def newer(cls, chat: BaseChat, message_id, limit: int):
        messages = cls.objects.filter(chat=chat, id__gt=message_id, is_deleted=False).order_by('created_at')[:limit]
        return [message.jsonl() for message in messages]

    def remove(self):
        self.is_deleted = True
        self.save()
