from django.db import models

from Chat.models import BaseChat, GroupChat
from Message.validators import MessageErrors
from User.models import BaseUser
from utils.choice import Choice
from utils.jsonify import Jsonify


class MessageTypeChoice(Choice):
    TEXT = 0
    IMAGE = 1
    FILE = 2
    SYSTEM = 3


class Message(models.Model, Jsonify):
    chat = models.ForeignKey(BaseChat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(BaseUser, on_delete=models.CASCADE)

    type = models.IntegerField(choices=MessageTypeChoice.to_choices())
    content = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-created_at"]  # 默认按时间倒序排列

    @classmethod
    def create(cls, chat: BaseChat, user: BaseUser, message_type, content):
        if user == chat.host or (isinstance(chat, GroupChat) and user in chat.guests.all()):
            return cls.objects.create(chat=chat, user=user, type=message_type, content=content)
        raise MessageErrors.NOT_A_MEMBER

    def _jsonify_user(self):
        return self.user.specify().tiny_json()

    def _jsonify_created_at(self):
        return self.created_at.timestamp()

    def jsonl(self):
        return self.jsonify('id->message_id', 'user', 'type', 'content', 'created_at')

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
