from typing import List

from diq import Dictify
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from smartdjango import Choice

from Chat.validators import ChatErrors
from User.models import HostUser, GuestUser, BaseUser


class ChatSchemeChoice(Choice):
    SINGLE = 0
    GROUP = 1


class BaseChat(models.Model, Dictify):
    host = models.ForeignKey(HostUser, on_delete=models.CASCADE)
    scheme = models.IntegerField(choices=ChatSchemeChoice.to_choices())

    created_at = models.DateTimeField(auto_now_add=True)
    last_chat_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    @classmethod
    def get_class(cls, scheme):
        return SingleChat if scheme == ChatSchemeChoice.SINGLE else GroupChat

    @classmethod
    def index(cls, chat_id):
        chats = cls.objects.filter(id=chat_id, is_deleted=False)
        if not chats.exists():
            raise ChatErrors.NOT_EXISTS(chat=chat_id)
        chat = chats.first()
        if type(chat) is BaseChat:
            return cls.get_class(chat.scheme).index(chat_id)
        return chat

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_last_chat_at(self):
        return self.last_chat_at.timestamp()

    def _dictify_last_message(self):
        from Message.models import Message
        messages = Message.objects.filter(chat=self, is_deleted=False).order_by('-created_at')
        if messages.exists():
            return messages.first().jsonl()
        return None

    def _dictify_host(self):
        return self.host.json()

    def json(self):
        raise NotImplementedError

    def jsonl(self):
        raise NotImplementedError

    @classmethod
    def get_host_chats(cls, host: HostUser):
        raise NotImplementedError

    @classmethod
    def get_guest_chats(cls, guest: GuestUser):
        raise NotImplementedError

    @property
    def group(self):
        return self.scheme == ChatSchemeChoice.GROUP

    def remove(self):
        self.is_deleted = True
        self.save()

    def specify(self):
        if type(self) is BaseChat:
            return self.get_class(self.scheme).index(self.id)
        return self


class SingleChat(BaseChat):
    guest = models.OneToOneField(GuestUser, on_delete=models.CASCADE)

    @classmethod
    def get_or_create(cls, guest: GuestUser):
        chats = cls.objects.filter(guest=guest, is_deleted=False)
        if chats.exists():
            return chats.first()
        return cls.objects.create(guest=guest, host=guest.host, scheme=ChatSchemeChoice.SINGLE)

    def _dictify_guest(self):
        return self.guest.json()

    def json(self):
        return self.dictify('host', 'guest', 'created_at', 'last_chat_at', 'group', 'id->chat_id', 'last_message')

    def jsonl(self):
        return self.json()

    @classmethod
    def get_host_chats(cls, host: HostUser):
        chats = cls.objects.filter(host=host, is_deleted=False)
        return [chat.jsonl() for chat in chats]

    @classmethod
    def get_guest_chats(cls, guest: GuestUser):
        chats = cls.objects.filter(guest=guest, is_deleted=False)
        return [chat.jsonl() for chat in chats]


class GroupChat(BaseChat):
    guests = models.ManyToManyField(GuestUser)
    name = models.CharField(max_length=20)

    @classmethod
    def create(cls, host: HostUser, guests: List[GuestUser]):
        for guest in guests:
            if guest.host != host:
                raise ChatErrors.UNALIGNED_HOST
            if guest.is_deleted:
                raise ChatErrors.GUEST_DELETED(guest=guest.name)

        num = len(guests) + 1
        name = _('Group Chat ({num})').format(num=num)
        chat = cls.objects.create(name=name, host=host, scheme=ChatSchemeChoice.GROUP)
        chat.guests.add(*guests)
        return chat

    def rename(self, name: str):
        print('wow')
        self.name = name
        self.save()

    def add_guest(self, guest: GuestUser):
        if guest.host != self.host:
            raise ChatErrors.UNALIGNED_HOST
        if guest.is_deleted:
            raise ChatErrors.GUEST_DELETED(guest=guest.name)
        self.guests.add(guest)

    def remove_guest(self, guest: GuestUser):
        if guest not in self.guests.all():
            raise ChatErrors.NOT_MEMBER(guest=guest.name, chat=self.name)

        self.guests.remove(guest)

    def _dictify_guests(self):
        return [guest.json() for guest in self.guests.all()]

    def json(self):
        return self.dictify('host', 'guests', 'name', 'created_at', 'last_chat_at', 'group', 'id->chat_id', 'last_message')

    def jsonl(self):
        return self.json()

    @classmethod
    def get_host_chats(cls, host: HostUser):
        chats = cls.objects.filter(host=host, is_deleted=False)
        return [chat.jsonl() for chat in chats]

    @classmethod
    def get_guest_chats(cls, guest: GuestUser):
        chats = cls.objects.filter(guests=guest, is_deleted=False)
        return [chat.jsonl() for chat in chats]


class ChatReadState(models.Model):
    chat = models.ForeignKey(BaseChat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(BaseUser, on_delete=models.CASCADE, db_index=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('chat', 'user')

    @classmethod
    def mark_read(cls, chat: BaseChat, user: BaseUser):
        state, _created = cls.objects.get_or_create(chat=chat, user=user)
        state.last_read_at = timezone.now()
        state.save(update_fields=['last_read_at'])
        return state

    @classmethod
    def get_last_read_at(cls, chat: BaseChat, user: BaseUser):
        state = cls.objects.filter(chat=chat, user=user).first()
        return state.last_read_at if state else None

    @classmethod
    def unread_count(cls, chat: BaseChat, user: BaseUser):
        from Message.models import Message
        last_read_at = cls.get_last_read_at(chat, user)
        if last_read_at is None:
            return Message.objects.filter(chat=chat, is_deleted=False).count()
        return Message.objects.filter(chat=chat, is_deleted=False, created_at__gt=last_read_at).count()
