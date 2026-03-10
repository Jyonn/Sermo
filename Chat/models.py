from typing import List

from diq import Dictify
from django.db import models, transaction
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
    owner = models.ForeignKey(
        BaseUser,
        on_delete=models.CASCADE,
        related_name='owned_group_chats',
        null=True,
        blank=True,
    )

    @classmethod
    def create(cls, creator: BaseUser, guests: List[GuestUser]):
        creator = creator.specify()
        host = creator if isinstance(creator, HostUser) else creator.host
        for guest in guests:
            if guest.host != host:
                raise ChatErrors.UNALIGNED_HOST
            if guest.is_deleted:
                raise ChatErrors.GUEST_DELETED(guest=guest.name)

        normalized_guests = {guest.id: guest for guest in guests}
        if isinstance(creator, GuestUser):
            normalized_guests[creator.id] = creator

        num = len(normalized_guests) + 1
        name = _('Group Chat ({num})').format(num=num)
        chat = cls.objects.create(
            name=name,
            host=host,
            owner=creator,
            scheme=ChatSchemeChoice.GROUP,
        )

        if isinstance(creator, HostUser):
            chat.guests.add(*normalized_guests.values())
        else:
            chat.guests.add(creator)
            for guest in normalized_guests.values():
                if guest.id == creator.id:
                    continue
                GroupChatInvite.invite(chat=chat, guest=guest, invited_by=creator, auto_accept=False)
        return chat

    def rename(self, name: str):
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
        GroupChatInvite.objects.filter(chat=self, guest=guest, status=GroupChatInviteStatusChoice.PENDING).update(
            status=GroupChatInviteStatusChoice.REJECTED
        )

    def _can_manage(self, user: BaseUser):
        user = user.specify()
        owner_id = self.owner_id or self.host_id
        return user.id == owner_id or user.id == self.host_id

    def invite_guest(self, inviter: BaseUser, guest: GuestUser):
        if not self._can_manage(inviter):
            raise ChatErrors.FORBIDDEN
        if guest.host_id != self.host_id:
            raise ChatErrors.UNALIGNED_HOST
        if guest.is_deleted:
            raise ChatErrors.GUEST_DELETED(guest=guest.name)
        auto_accept = inviter.id == self.host_id
        return GroupChatInvite.invite(
            chat=self,
            guest=guest,
            invited_by=inviter,
            auto_accept=auto_accept,
        )

    def respond_invite(self, guest: GuestUser, accept: bool):
        if guest.host_id != self.host_id:
            raise ChatErrors.UNALIGNED_HOST
        return GroupChatInvite.respond(chat=self, guest=guest, accept=accept)

    def _dictify_guests(self):
        return [guest.json() for guest in self.guests.all()]

    def _dictify_owner(self):
        if self.owner_id:
            return self.owner.specify().tiny_json()
        return self.host.tiny_json()

    def json(self):
        return self.dictify(
            'host',
            'owner',
            'guests',
            'name',
            'created_at',
            'last_chat_at',
            'group',
            'id->chat_id',
            'last_message'
        )

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


class GroupChatInviteStatusChoice(Choice):
    PENDING = 0
    ACCEPTED = 1
    REJECTED = 2


class GroupChatInvite(models.Model, Dictify):
    chat = models.ForeignKey(GroupChat, on_delete=models.CASCADE, related_name='invites')
    guest = models.ForeignKey(GuestUser, on_delete=models.CASCADE, related_name='group_invites')
    invited_by = models.ForeignKey(BaseUser, on_delete=models.CASCADE, related_name='sent_group_invites')
    status = models.IntegerField(
        choices=GroupChatInviteStatusChoice.to_choices(),
        default=GroupChatInviteStatusChoice.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('chat', 'guest')

    @classmethod
    def invite(cls, chat: GroupChat, guest: GuestUser, invited_by: BaseUser, auto_accept: bool = False):
        if chat.guests.filter(id=guest.id).exists():
            invite, _created = cls.objects.get_or_create(
                chat=chat,
                guest=guest,
                defaults=dict(
                    invited_by=invited_by,
                    status=GroupChatInviteStatusChoice.ACCEPTED,
                ),
            )
            if invite.status != GroupChatInviteStatusChoice.ACCEPTED:
                invite.status = GroupChatInviteStatusChoice.ACCEPTED
                invite.invited_by = invited_by
                invite.save(update_fields=['status', 'invited_by'])
            return invite

        invite, created = cls.objects.get_or_create(
            chat=chat,
            guest=guest,
            defaults=dict(
                invited_by=invited_by,
                status=GroupChatInviteStatusChoice.ACCEPTED if auto_accept else GroupChatInviteStatusChoice.PENDING,
            ),
        )
        if not created:
            if invite.status == GroupChatInviteStatusChoice.PENDING and not auto_accept:
                raise ChatErrors.INVITE_PENDING(guest=guest.name)
            invite.invited_by = invited_by
            invite.status = GroupChatInviteStatusChoice.ACCEPTED if auto_accept else GroupChatInviteStatusChoice.PENDING
            invite.save(update_fields=['invited_by', 'status'])

        if auto_accept:
            chat.add_guest(guest)
            return invite

        from User.models import NotificationEvent
        NotificationEvent.emit_system_event(
            user=guest,
            actor=invited_by,
            payload=dict(
                kind='group_invite',
                chat_id=chat.id,
                chat_name=chat.name,
            ),
        )
        return invite

    @classmethod
    def respond(cls, chat: GroupChat, guest: GuestUser, accept: bool):
        invite = cls.objects.filter(chat=chat, guest=guest).first()
        if invite is None:
            raise ChatErrors.INVITE_NOT_FOUND
        if invite.status != GroupChatInviteStatusChoice.PENDING:
            raise ChatErrors.INVITE_CLOSED

        with transaction.atomic():
            invite.status = GroupChatInviteStatusChoice.ACCEPTED if accept else GroupChatInviteStatusChoice.REJECTED
            invite.save(update_fields=['status'])
            if accept:
                chat.add_guest(guest)

        from User.models import NotificationEvent
        NotificationEvent.emit_system_event(
            user=invite.invited_by,
            actor=guest,
            payload=dict(
                kind='group_invite_response',
                chat_id=chat.id,
                accepted=bool(accept),
                guest=guest.tiny_json(),
            ),
        )
        return invite
