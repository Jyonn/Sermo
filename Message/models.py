from smartdjango import models, Choice

from Chat.models import Chat
from Message.validators import MessageErrors, MessageValidator
from User.models import User


class MessageTypeChoice(Choice):
    TEXT = 0
    IMAGE = 1
    FILE = 2
    SYSTEM = 3


class Message(models.Model):
    vldt = MessageValidator

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    type = models.IntegerField(choices=MessageTypeChoice.to_choices())
    content = models.CharField(max_length=vldt.MAX_CONTENT_LENGTH)

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
            return cls.objects.create(chat=chat, user=user, type=message_type, content=content)
        raise MessageErrors.NOT_A_MEMBER

    def _dictify_user(self):
        return self.user.tiny_json()

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def jsonl(self):
        return self.dictify('id->message_id', 'user', 'type', 'content', 'created_at')

    @classmethod
    def index(cls, message_id):
        try:
            return cls.objects.get(id=message_id, is_deleted=False)
        except cls.DoesNotExist:
            raise MessageErrors.NOT_EXISTS

    @classmethod
    def latest(cls, chat: Chat, limit: int):
        messages = cls.visible_in_chat(chat).order_by('-created_at')[:limit]
        return [message.jsonl() for message in messages]

    @classmethod
    def older(cls, chat: Chat, message_id, limit: int):
        messages = cls.visible_in_chat(chat).filter(id__lt=message_id).order_by('-created_at')[:limit]
        return [message.jsonl() for message in messages]

    @classmethod
    def newer(cls, chat: Chat, message_id, limit: int):
        messages = cls.visible_in_chat(chat).filter(id__gt=message_id).order_by('created_at')[:limit]
        return [message.jsonl() for message in messages]

    @classmethod
    def sync_for_user(cls, user: User, after: int, limit: int):
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
            payload = message.jsonl()
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
