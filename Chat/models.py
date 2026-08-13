from typing import List

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from smartdjango import models, Choice

from Chat.validators import ChatErrors, ChatMemberErrors, ChatValidator, ChatMemberValidator
from User.models import User


class ChatTypeChoice(Choice):
    DIRECT = 0
    GROUP = 1


class ChatMemberRoleChoice(Choice):
    MEMBER = 0
    OWNER = 1


class ChatMemberStatusChoice(Choice):
    PENDING = 0
    ACTIVE = 1
    LEFT = 2
    REJECTED = 3
    KICKED = 4


class Chat(models.Model):
    validators = ChatValidator
    vldt = ChatValidator

    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='chats', db_index=True)
    chat_type = models.IntegerField(choices=ChatTypeChoice.to_choices(), db_index=True)
    title = models.CharField(max_length=vldt.TITLE_MAX_LENGTH, null=True, blank=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_chats')

    created_at = models.DateTimeField(auto_now_add=True)
    last_chat_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    @classmethod
    def index(cls, chat_id):
        try:
            return cls.objects.get(id=chat_id, is_deleted=False)
        except cls.DoesNotExist:
            raise ChatErrors.NOT_EXISTS(chat=chat_id)

    @property
    def group(self):
        return self.chat_type == ChatTypeChoice.GROUP

    @property
    def direct(self):
        return self.chat_type == ChatTypeChoice.DIRECT

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_last_chat_at(self):
        return self.last_chat_at.timestamp()

    def _dictify_last_message(self):
        from Message.models import Message
        message = Message.latest_preview_for_user(self)
        if message is not None:
            return message.jsonl()
        return None

    def _dictify_members(self):
        members = ChatMember.objects.filter(
            chat=self,
            status=ChatMemberStatusChoice.ACTIVE,
            user__is_deleted=False,
        ).select_related('user').order_by('user__name_pinyin', 'user__lower_name', 'user_id')
        return [item.user.jsonl() for item in members]

    def _dictify_owner(self):
        owner = ChatMember.objects.filter(
            chat=self,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
            user__is_deleted=False,
        ).select_related('user').first()
        return owner.user.tiny_json() if owner else None

    def json(self):
        return self.dictify(
            'id->chat_id',
            'chat_type',
            'title',
            'owner',
            'members',
            'group',
            'created_at',
            'last_chat_at',
            'last_message',
        )

    def jsonl(self):
        return self.json()

    def remove(self):
        self.is_deleted = True
        self.save(update_fields=['is_deleted'])

    def has_active_member(self, user: User):
        member_exists = ChatMember.objects.filter(
            chat=self,
            user=user,
            status=ChatMemberStatusChoice.ACTIVE,
        ).exists()
        if not member_exists:
            return False
        if not self.direct:
            return True
        return self._direct_friendship_valid()

    def is_owner(self, user: User):
        return ChatMember.objects.filter(
            chat=self,
            user=user,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
        ).exists()

    @classmethod
    def get_user_chats(cls, user: User):
        chats = list(
            cls.objects.filter(
                is_deleted=False,
                chat_members__user=user,
                chat_members__status=ChatMemberStatusChoice.ACTIVE,
            ).distinct()
        )
        return [
            chat for chat in chats
            if chat.has_active_member(user)
            and not (chat.group and not user.verified and user.space.unverified_group_policy < 1)
        ]

    @classmethod
    def _pair(cls, self_user: User, peer_user: User):
        if self_user.id == peer_user.id:
            raise ChatErrors.FORBIDDEN
        if self_user.space_id != peer_user.space_id:
            raise ChatErrors.UNALIGNED_SPACE
        if self_user.id < peer_user.id:
            return self_user, peer_user
        return peer_user, self_user

    @classmethod
    def _has_friendship(cls, user_low: User, user_high: User):
        from Friendship.models import Friendship, FriendshipStatusChoice

        return Friendship.objects.filter(
            space=user_low.space,
            user_low=user_low,
            user_high=user_high,
            status=FriendshipStatusChoice.ACCEPTED,
        ).exists()

    @classmethod
    def _require_friendship(cls, user_low: User, user_high: User):
        if not cls._has_friendship(user_low, user_high):
            raise ChatErrors.NOT_FRIENDS

    @classmethod
    def _require_verified_group_operator(cls, operator: User):
        if not operator.verified:
            raise ChatErrors.CREATOR_NOT_VERIFIED

    @classmethod
    def _require_friend_of(cls, owner: User, target: User):
        user_low, user_high = cls._pair(owner, target)
        if not cls._has_friendship(user_low, user_high):
            raise ChatErrors.TARGET_NOT_FRIEND(user=target.name)

    def _direct_friendship_valid(self):
        if not self.direct:
            return True
        active_member_ids = list(
            ChatMember.objects.filter(
                chat=self,
                status=ChatMemberStatusChoice.ACTIVE,
            ).values_list('user_id', flat=True)
        )
        if len(active_member_ids) != 2:
            return False
        user_low_id, user_high_id = sorted(active_member_ids)
        from Friendship.models import Friendship, FriendshipStatusChoice
        return Friendship.objects.filter(
            space_id=self.space_id,
            user_low_id=user_low_id,
            user_high_id=user_high_id,
            status=FriendshipStatusChoice.ACCEPTED,
        ).exists()

    @classmethod
    def get_or_create_direct(cls, self_user: User, peer_user: User):
        user_low, user_high = cls._pair(self_user, peer_user)
        cls._require_friendship(user_low, user_high)
        direct_chats = cls.objects.filter(
            space_id=user_low.space_id,
            chat_type=ChatTypeChoice.DIRECT,
            is_deleted=False,
        )
        for chat in direct_chats:
            active_member_ids = list(
                ChatMember.objects.filter(chat=chat, status=ChatMemberStatusChoice.ACTIVE)
                .values_list('user_id', flat=True)
            )
            if len(active_member_ids) == 2 and set(active_member_ids) == {user_low.id, user_high.id}:
                return chat

        with transaction.atomic():
            chat = cls.objects.create(
                space_id=user_low.space_id,
                chat_type=ChatTypeChoice.DIRECT,
                title=None,
                created_by=self_user,
            )
            ChatMember.objects.create(
                chat=chat,
                user=user_low,
                role=ChatMemberRoleChoice.MEMBER,
                status=ChatMemberStatusChoice.ACTIVE,
                invited_by=self_user,
                joined_at=timezone.now(),
            )
            ChatMember.objects.create(
                chat=chat,
                user=user_high,
                role=ChatMemberRoleChoice.MEMBER,
                status=ChatMemberStatusChoice.ACTIVE,
                invited_by=self_user,
                joined_at=timezone.now(),
            )
            return chat

    @classmethod
    def create_group(cls, creator: User, users: List[User], title: str = None):
        creator.space.require_chat_enabled()
        creator.require_growth_capability('create_group')
        cls._require_verified_group_operator(creator)
        normalized = {creator.id: creator}
        for user in users:
            if user.space_id != creator.space_id:
                raise ChatErrors.UNALIGNED_SPACE
            if user.is_deleted:
                raise ChatErrors.USER_DELETED(user=user.name)
            if user.id != creator.id:
                cls._require_friend_of(creator, user)
                creator.space.require_group_join_allowed(user)
            normalized[user.id] = user

        final_title = (title or '').strip()
        if not final_title:
            final_title = _('Group Chat')

        with transaction.atomic():
            chat = cls.objects.create(
                space=creator.space,
                chat_type=ChatTypeChoice.GROUP,
                title=final_title,
                created_by=creator,
            )
            ChatMember.objects.create(
                chat=chat,
                user=creator,
                role=ChatMemberRoleChoice.OWNER,
                status=ChatMemberStatusChoice.ACTIVE,
                invited_by=creator,
                joined_at=timezone.now(),
            )
            for user in normalized.values():
                if user.id == creator.id:
                    continue
                ChatMember.invite(chat=chat, user=user, invited_by=creator)
            from Message.models import Message
            invited_names = [user.name for user in normalized.values() if user.id != creator.id]
            Message.create_system(
                chat,
                creator,
                'group_created',
                member_names=invited_names,
                member_count=len(invited_names),
            )
            creator.award_growth('explore:create_group')
            return chat

    def rename(self, operator: User, title: str):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        operator.require_growth_capability('rename_group')
        next_title = (title or '').strip() or self.title
        if next_title == self.title:
            return
        previous_title = self.title
        with transaction.atomic():
            self.title = next_title
            self.save(update_fields=['title'])
            from Message.models import Message
            Message.create_system(
                self,
                operator,
                'group_renamed',
                old_title=previous_title,
                new_title=next_title,
            )

    def invite_member(self, inviter: User, user: User):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        inviter.require_growth_capability('invite_group_member')
        self._require_verified_group_operator(inviter)
        if user.space_id != self.space_id:
            raise ChatErrors.UNALIGNED_SPACE
        if user.is_deleted:
            raise ChatErrors.USER_DELETED(user=user.name)
        if user.id != inviter.id:
            self._require_friend_of(inviter, user)
            self.space.require_group_join_allowed(user)
        return ChatMember.invite(chat=self, user=user, invited_by=inviter)

    def invite_members(self, inviter: User, users: List[User]):
        with transaction.atomic():
            members = [self.invite_member(inviter, user) for user in users]
            if members:
                from Message.models import Message
                Message.create_system(
                    self,
                    inviter,
                    'members_invited',
                    member_names=[member.user.name for member in members],
                    member_count=len(members),
                )
            return members

    def respond_invite(self, user: User, accept: bool):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        return ChatMember.respond(chat=self, user=user, accept=accept)

    def remove_member(self, operator: User, user: User):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        if not self.is_owner(operator):
            raise ChatErrors.FORBIDDEN
        return ChatMember.kick(chat=self, user=user)

    def remove_members(self, operator: User, users: List[User]):
        with transaction.atomic():
            members = [self.remove_member(operator, user) for user in users]
            if members:
                from Message.models import Message
                Message.create_system(
                    self,
                    operator,
                    'members_removed',
                    member_names=[member.user.name for member in members],
                    member_count=len(members),
                )
            return members

    def leave(self, user: User):
        member = ChatMember.objects.filter(
            chat=self,
            user=user,
            status=ChatMemberStatusChoice.ACTIVE,
        ).first()
        if member is None:
            raise ChatMemberErrors.NOT_MEMBER(user=user.name, chat=self.id)
        if self.group and member.role == ChatMemberRoleChoice.OWNER:
            raise ChatMemberErrors.OWNER_LEAVE_FORBIDDEN
        with transaction.atomic():
            if self.group:
                from Message.models import Message
                Message.create_system(self, user, 'member_left')
            member.status = ChatMemberStatusChoice.LEFT
            member.left_at = timezone.now()
            member.save(update_fields=['status', 'left_at', 'updated_at'])
        return member


class ChatMember(models.Model):
    validators = ChatMemberValidator
    vldt = ChatMemberValidator

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='chat_members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_memberships')
    role = models.IntegerField(
        choices=ChatMemberRoleChoice.to_choices(),
        default=ChatMemberRoleChoice.MEMBER,
    )
    status = models.IntegerField(
        choices=ChatMemberStatusChoice.to_choices(),
        default=ChatMemberStatusChoice.PENDING,
        db_index=True,
    )
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_chat_invites',
    )
    joined_at = models.DateTimeField(null=True, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['chat', 'user'], name='unique_chat_member'),
        ]

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_updated_at(self):
        return self.updated_at.timestamp()

    def _dictify_user(self):
        return self.user.tiny_json()

    def _dictify_invited_by(self):
        return self.invited_by.tiny_json() if self.invited_by_id else None

    def _dictify_chat_id(self):
        return self.chat_id

    def _dictify_chat(self):
        return self.chat.jsonl()

    def json(self):
        return self.dictify('chat_id', 'chat', 'user', 'invited_by', 'role', 'status', 'created_at', 'updated_at')

    @classmethod
    def index(cls, member_id):
        try:
            return cls.objects.get(id=member_id)
        except cls.DoesNotExist:
            raise ChatMemberErrors.NOT_EXISTS(chat=member_id)

    @classmethod
    def invite(cls, chat: Chat, user: User, invited_by: User):
        member = cls.objects.filter(chat=chat, user=user).first()
        if member is None:
            member = cls.objects.create(
                chat=chat,
                user=user,
                role=ChatMemberRoleChoice.MEMBER,
                status=ChatMemberStatusChoice.ACTIVE,
                invited_by=invited_by,
                joined_at=timezone.now(),
            )
            from User.models import NotificationEvent
            NotificationEvent.emit_system_event(
                user=user,
                actor=invited_by,
                payload=dict(kind='group_invite', chat_id=chat.id, chat_name=chat.title),
            )
            return member

        if member.status == ChatMemberStatusChoice.ACTIVE:
            raise ChatMemberErrors.ALREADY_MEMBER(user=user.name, chat=chat.id)

        member.role = ChatMemberRoleChoice.MEMBER
        member.status = ChatMemberStatusChoice.ACTIVE
        member.invited_by = invited_by
        member.joined_at = timezone.now()
        member.left_at = None
        member.save(update_fields=['role', 'status', 'invited_by', 'joined_at', 'left_at', 'updated_at'])
        from User.models import NotificationEvent
        NotificationEvent.emit_system_event(
            user=user,
            actor=invited_by,
            payload=dict(kind='group_invite', chat_id=chat.id, chat_name=chat.title),
        )
        return member

    @classmethod
    def respond(cls, chat: Chat, user: User, accept: bool):
        member = cls.objects.filter(chat=chat, user=user).first()
        if member is None:
            raise ChatMemberErrors.INVITE_NOT_FOUND
        if member.status != ChatMemberStatusChoice.PENDING:
            raise ChatMemberErrors.INVITE_CLOSED

        if accept:
            member.status = ChatMemberStatusChoice.ACTIVE
            member.joined_at = timezone.now()
            member.left_at = None
            member.save(update_fields=['status', 'joined_at', 'left_at', 'updated_at'])
        else:
            member.status = ChatMemberStatusChoice.REJECTED
            member.left_at = timezone.now()
            member.save(update_fields=['status', 'left_at', 'updated_at'])

        from User.models import NotificationEvent
        if member.invited_by_id:
            NotificationEvent.emit_system_event(
                user=member.invited_by,
                actor=user,
                payload=dict(
                    kind='group_invite_response',
                    chat_id=chat.id,
                    accepted=bool(accept),
                    user=user.tiny_json(),
                ),
            )
        return member

    @classmethod
    def kick(cls, chat: Chat, user: User):
        member = cls.objects.filter(chat=chat, user=user).first()
        if member is None:
            raise ChatMemberErrors.INVITE_NOT_FOUND
        member.status = ChatMemberStatusChoice.KICKED
        member.left_at = timezone.now()
        member.save(update_fields=['status', 'left_at', 'updated_at'])
        return member

    @classmethod
    def pending_for_user(cls, user: User, limit: int = 100):
        rows = cls.objects.filter(
            user=user,
            status=ChatMemberStatusChoice.PENDING,
            chat__is_deleted=False,
        ).select_related('chat', 'invited_by').order_by('-created_at')[:limit]
        return [row.json() for row in rows]


class ChatReadState(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, db_index=True)
    last_read_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('chat', 'user')

    @classmethod
    def mark_read(cls, chat: Chat, user: User):
        state, _created = cls.objects.get_or_create(chat=chat, user=user)
        state.last_read_at = timezone.now()
        state.save(update_fields=['last_read_at', 'updated_at'])
        return state

    @classmethod
    def get_last_read_at(cls, chat: Chat, user: User):
        state = cls.objects.filter(chat=chat, user=user).first()
        return state.last_read_at if state else None

    @classmethod
    def unread_count(cls, chat: Chat, user: User):
        from Message.models import Message, MessageTypeChoice
        last_read_at = cls.get_last_read_at(chat, user)
        unread_messages = Message.visible_for_user(chat, user).exclude(
            user=user,
        ).exclude(type=MessageTypeChoice.SYSTEM)
        if last_read_at is None:
            return unread_messages.count()
        return unread_messages.filter(created_at__gt=last_read_at).count()

    @classmethod
    def has_unread_mention(cls, chat: Chat, user: User):
        last_read_at = cls.get_last_read_at(chat, user)
        from Message.models import Message
        visible_message_ids = Message.visible_for_user(chat, user).values_list('id', flat=True)
        mentions = ChatMessageMention.objects.filter(
            chat=chat,
            user=user,
            message_id__in=visible_message_ids,
        )
        if last_read_at is not None:
            mentions = mentions.filter(message__created_at__gt=last_read_at)
        return mentions.exists()


class ChatUserPreference(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='user_preferences', db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_preferences', db_index=True)
    pinned = models.BooleanField(default=False)
    online_reminder_enabled = models.BooleanField(default=False)
    notifications_muted = models.BooleanField(default=False)
    unread_badge_muted = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('chat', 'user')

    @classmethod
    def ensure(cls, chat: Chat, user: User):
        preference, _created = cls.objects.get_or_create(chat=chat, user=user)
        return preference

    @classmethod
    def update(cls, chat: Chat, user: User, pinned=None, online_reminder_enabled=None, notifications_muted=None, unread_badge_muted=None):
        preference = cls.ensure(chat, user)
        updates = []
        if pinned is not None:
            preference.pinned = bool(pinned)
            updates.append('pinned')
        if online_reminder_enabled is not None:
            if chat.group and bool(online_reminder_enabled):
                raise ChatErrors.NOT_DIRECT_CHAT(chat=chat.id)
            preference.online_reminder_enabled = bool(online_reminder_enabled)
            updates.append('online_reminder_enabled')
        if notifications_muted is not None:
            if not chat.group and bool(notifications_muted):
                raise ChatErrors.NOT_GROUP_CHAT(chat=chat.id)
            preference.notifications_muted = bool(notifications_muted)
            updates.append('notifications_muted')
        if unread_badge_muted is not None:
            if not chat.group and bool(unread_badge_muted):
                raise ChatErrors.NOT_GROUP_CHAT(chat=chat.id)
            if bool(unread_badge_muted) and not preference.notifications_muted:
                preference.notifications_muted = True
                updates.append('notifications_muted')
            preference.unread_badge_muted = bool(unread_badge_muted)
            updates.append('unread_badge_muted')
        if notifications_muted is not None and not bool(notifications_muted) and preference.unread_badge_muted:
            preference.unread_badge_muted = False
            updates.append('unread_badge_muted')
        if updates:
            preference.save(update_fields=[*dict.fromkeys(updates), 'updated_at'])
        return preference

    def json(self):
        return self.dictify('pinned', 'online_reminder_enabled', 'notifications_muted', 'unread_badge_muted')

    @classmethod
    def emit_peer_online_events(cls, peer: User):
        from User.models import NotificationEvent

        preferences = cls.objects.filter(
            online_reminder_enabled=True,
            chat__chat_type=ChatTypeChoice.DIRECT,
            chat__is_deleted=False,
            chat__chat_members__user=peer,
            chat__chat_members__status=ChatMemberStatusChoice.ACTIVE,
            user__is_deleted=False,
        ).select_related('chat', 'user').distinct()
        events = []
        for preference in preferences:
            if preference.user_id == peer.id or not preference.chat.has_active_member(preference.user):
                continue
            event = NotificationEvent.emit_system_event(
                user=preference.user,
                actor=peer,
                payload=dict(kind='peer_online', chat_id=preference.chat_id),
            )
            events.append(event)
        return events


class ChatMessageMention(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='message_mentions', db_index=True)
    message = models.ForeignKey('Message.Message', on_delete=models.CASCADE, related_name='chat_mentions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_mentions', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['message', 'user'], name='unique_message_mention_user'),
        ]

    @classmethod
    def record(cls, message, user_ids):
        if not message.chat.group or not user_ids:
            return []
        active_user_ids = set(ChatMember.objects.filter(
            chat=message.chat,
            status=ChatMemberStatusChoice.ACTIVE,
            user_id__in=set(user_ids),
        ).exclude(user_id=message.user_id).values_list('user_id', flat=True))
        return cls.objects.bulk_create([
            cls(chat=message.chat, message=message, user_id=user_id)
            for user_id in active_user_ids
        ], ignore_conflicts=True)
