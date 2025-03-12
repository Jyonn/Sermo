from typing import List

from django.db import models
from django.utils.translation import gettext as _

from Chat.validators import ChatErrors
from User.models import HostUser, GuestUser, BaseUser
from utils.choice import Choice
from utils.jsonify import Jsonify


class ChatSchemeChoice(Choice):
    SINGLE = 0
    GROUP = 1


class BaseChat(models.Model, Jsonify):
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

    def _jsonify_created_at(self):
        return self.created_at.timestamp()

    def _jsonify_last_chat_at(self):
        return self.last_chat_at.timestamp()

    def _jsonify_host(self):
        return self.host.tiny_json()

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


class SingleChat(BaseChat):
    guest = models.ForeignKey(GuestUser, on_delete=models.CASCADE, unique=True, db_index=True)

    @classmethod
    def get_or_create(cls, guest: GuestUser):
        chats = cls.objects.filter(guest=guest, is_deleted=False)
        if chats.exists():
            return chats.first()
        return cls.objects.create(guest=guest, host=guest.host, scheme=ChatSchemeChoice.SINGLE)

    def _jsonify_guest(self):
        return self.guest.tiny_json()

    def _jsonify_group(self):
        return False

    def json(self):
        return self.jsonify('host', 'guest', 'created_at', 'last_chat_at', 'group', 'id->chat_id')

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
        if len(guests) == 0:
            raise ChatErrors.GROUP_CHAT_EMPTY
        if len(guests) == 1:
            raise ChatErrors.GROUP_CHAT_TOO_SMALL

        for guest in guests:
            if guest.host != host:
                raise ChatErrors.UNALIGNED_HOST

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
        self.guests.add(guest)

    def remove_guest(self, guest: GuestUser):
        if guest not in self.guests.all():
            raise ChatErrors.NOT_MEMBER(guest=guest.name, chat=self.name)

        self.guests.remove(guest)

    def _jsonify_guests(self):
        return [guest.tiny_json() for guest in self.guests.all()]

    def _jsonify_group(self):
        return True

    def json(self):
        return self.jsonify('host', 'guests', 'name', 'created_at', 'last_chat_at', 'group', 'id->chat_id')

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
