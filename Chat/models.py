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


class ChatPurposeChoice(Choice):
    NORMAL = 0
    SUBMISSION = 1


class ChatMemberRoleChoice(Choice):
    MEMBER = 0
    OWNER = 1


class ChatMemberStatusChoice(Choice):
    PENDING = 0
    ACTIVE = 1
    LEFT = 2
    REJECTED = 3
    KICKED = 4


class SubmissionStatusChoice(Choice):
    DRAFT = 0
    REVIEW = 1
    REVISION = 2
    TERMINATED = 3
    READY = 4
    PUBLISHED = 5


class Chat(models.Model):
    validators = ChatValidator
    vldt = ChatValidator

    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='chats', db_index=True)
    chat_type = models.IntegerField(choices=ChatTypeChoice.to_choices(), db_index=True)
    purpose = models.IntegerField(choices=ChatPurposeChoice.to_choices(), default=ChatPurposeChoice.NORMAL, db_index=True)
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

    @property
    def submission(self):
        return self.purpose == ChatPurposeChoice.SUBMISSION

    def _dictify_purpose(self):
        return 'submission' if self.submission else 'normal'

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
        ).select_related('user').order_by('joined_at', 'created_at', 'id')
        payload = []
        for item in members:
            user = item.user.jsonl()
            user['joined_at'] = item.joined_at.timestamp()
            payload.append(user)
        return payload

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
            'purpose',
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

    def _active_user_ids(self):
        return list(
            ChatMember.objects.filter(
                chat=self,
                status=ChatMemberStatusChoice.ACTIVE,
                user__is_deleted=False,
            ).values_list('user_id', flat=True)
        )

    def _emit_state_changed(self, extra_user_ids=None):
        from User.models import UserStateEvent, UserStateEventKindChoice

        user_ids = set(self._active_user_ids())
        user_ids.update(extra_user_ids or [])
        UserStateEvent.emit_many(user_ids, UserStateEventKindChoice.CHATS_CHANGED, self.id)

    def remove(self):
        if self.submission:
            raise ChatErrors.FORBIDDEN
        user_ids = self._active_user_ids()
        self.is_deleted = True
        self.save(update_fields=['is_deleted'])
        self._emit_state_changed(user_ids)

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
    def get_user_chats(cls, user: User, purpose=ChatPurposeChoice.NORMAL, submission_role=None):
        filters = dict(
            is_deleted=False,
            chat_members__user=user,
            chat_members__status=ChatMemberStatusChoice.ACTIVE,
            purpose=purpose,
        )
        if purpose == ChatPurposeChoice.SUBMISSION:
            if submission_role == 'reviewer':
                filters.update(
                    submission_record__recipient=user,
                    submission_record__status__gt=SubmissionStatusChoice.DRAFT,
                )
            else:
                filters['submission_record__author'] = user
        chats = list(
            cls.objects.filter(**filters).distinct()
        )
        return [
            chat for chat in chats
            if chat.has_active_member(user)
            and (not chat.group or user.has_capability('chat.group.join'))
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
            chat._emit_state_changed()
            return chat

    @classmethod
    def create_group(cls, creator: User, users: List[User], title: str = None):
        creator.space.require_chat_enabled()
        creator.require_capability('chat.group.create')
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
            chat._emit_state_changed()
            return chat

    @classmethod
    def create_submission(cls, creator: User, recipient: User, title: str, client_draft_id: str):
        creator.space.require_submission_enabled()
        if recipient.space_id != creator.space_id or recipient.is_deleted:
            raise ChatErrors.SUBMISSION_RECIPIENT_INVALID
        if not (recipient.is_official or recipient.is_space_operator):
            raise ChatErrors.SUBMISSION_RECIPIENT_INVALID
        normalized_title = (title or '').strip()
        if not normalized_title:
            raise ChatErrors.SUBMISSION_TITLE_REQUIRED

        existing = cls.objects.filter(
            space=creator.space,
            purpose=ChatPurposeChoice.SUBMISSION,
            created_by=creator,
            submission_record__client_draft_id=client_draft_id,
            is_deleted=False,
        ).first()
        if existing is not None:
            return existing, False

        from Friendship.models import Friendship, FriendshipStatusChoice
        space, user_low, user_high = Friendship._pair(creator, recipient)
        friendship, _created = Friendship.objects.get_or_create(
            space=space,
            user_low=user_low,
            user_high=user_high,
            defaults=dict(
                requested_by=creator,
                status=FriendshipStatusChoice.ACCEPTED,
                responded_at=timezone.now(),
                source=Friendship.SOURCE_DIRECT,
            ),
        )
        if friendship.status != FriendshipStatusChoice.ACCEPTED:
            friendship.status = FriendshipStatusChoice.ACCEPTED
            friendship.responded_at = timezone.now()
            friendship.save(update_fields=['status', 'responded_at', 'updated_at'])
        friendship._emit_state_changes(chats=True, friends=True, requests=True)

        chat = cls.objects.create(
            space=creator.space,
            chat_type=ChatTypeChoice.GROUP,
            purpose=ChatPurposeChoice.SUBMISSION,
            title=normalized_title,
            created_by=creator,
        )
        ChatMember.objects.create(
            chat=chat,
            user=creator,
            role=ChatMemberRoleChoice.MEMBER,
            status=ChatMemberStatusChoice.ACTIVE,
            invited_by=creator,
            joined_at=chat.created_at,
        )
        ChatMember.objects.create(
            chat=chat,
            user=recipient,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
            invited_by=creator,
            joined_at=chat.created_at,
        )
        Submission.objects.create(
            chat=chat,
            author=creator,
            recipient=recipient,
            client_draft_id=client_draft_id,
        )
        return chat, True

    def rename(self, operator: User, title: str):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        operator.require_capability('chat.group.rename')
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
            self._emit_state_changed()

    def invite_member(self, inviter: User, user: User):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        inviter.require_capability('chat.group.invite')
        if user.space_id != self.space_id:
            raise ChatErrors.UNALIGNED_SPACE
        if user.is_deleted:
            raise ChatErrors.USER_DELETED(user=user.name)
        if user.id != inviter.id:
            self._require_friend_of(inviter, user)
            self.space.require_group_join_allowed(user)
        member = ChatMember.request_submission_invite(chat=self, user=user, invited_by=inviter) \
            if self.submission else ChatMember.invite(chat=self, user=user, invited_by=inviter)
        self._emit_state_changed([user.id])
        return member

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
        member = ChatMember.respond(chat=self, user=user, accept=accept)
        self._emit_state_changed([user.id])
        return member

    def remove_member(self, operator: User, user: User):
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        if not self.is_owner(operator):
            raise ChatErrors.FORBIDDEN
        member = ChatMember.kick(chat=self, user=user)
        self._emit_state_changed([user.id])
        return member

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

    def transfer_ownership(self, operator: User, target: User):
        if self.submission:
            raise ChatErrors.FORBIDDEN
        if not self.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=self.id)
        if operator.id == target.id:
            raise ChatMemberErrors.OWNER_TRANSFER_TO_SELF

        with transaction.atomic():
            owner_member = ChatMember.objects.select_for_update().filter(
                chat=self,
                user=operator,
                role=ChatMemberRoleChoice.OWNER,
                status=ChatMemberStatusChoice.ACTIVE,
            ).first()
            if owner_member is None:
                raise ChatErrors.FORBIDDEN
            target_member = ChatMember.objects.select_for_update().filter(
                chat=self,
                user=target,
                status=ChatMemberStatusChoice.ACTIVE,
                user__is_deleted=False,
            ).first()
            if target_member is None:
                raise ChatMemberErrors.NOT_MEMBER(user=target.name, chat=self.id)

            owner_member.role = ChatMemberRoleChoice.MEMBER
            owner_member.save(update_fields=['role', 'updated_at'])
            target_member.role = ChatMemberRoleChoice.OWNER
            target_member.save(update_fields=['role', 'updated_at'])

            from Message.models import Message
            Message.create_system(
                self,
                operator,
                'ownership_transferred',
                new_owner_name=target.name,
            )
            self._emit_state_changed()
        return target_member

    def leave(self, user: User):
        if self.submission:
            raise ChatErrors.SUBMISSION_LEAVE_FORBIDDEN
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
            self._emit_state_changed([user.id])
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
    def request_submission_invite(cls, chat: Chat, user: User, invited_by: User):
        if not chat.submission:
            raise ChatErrors.FORBIDDEN
        member = cls.objects.filter(chat=chat, user=user).first()
        if member is not None and member.status == ChatMemberStatusChoice.ACTIVE:
            raise ChatMemberErrors.ALREADY_MEMBER(user=user.name, chat=chat.id)
        if member is not None and member.status == ChatMemberStatusChoice.PENDING:
            raise ChatMemberErrors.INVITE_PENDING(user=user.name, chat=chat.id)
        if member is None:
            member = cls(chat=chat, user=user)
        member.role = ChatMemberRoleChoice.MEMBER
        member.status = ChatMemberStatusChoice.PENDING
        member.invited_by = invited_by
        member.joined_at = None
        member.left_at = None
        member.save()
        return member

    def review_submission_invite(self, reviewer: User, accept: bool):
        if not self.chat.submission or not (reviewer.is_official or reviewer.is_space_operator):
            raise ChatErrors.SUBMISSION_REVIEW_FORBIDDEN
        if reviewer.space_id != self.chat.space_id:
            raise ChatErrors.SUBMISSION_REVIEW_FORBIDDEN
        if self.status != ChatMemberStatusChoice.PENDING:
            raise ChatMemberErrors.INVITE_CLOSED
        self.status = ChatMemberStatusChoice.ACTIVE if accept else ChatMemberStatusChoice.REJECTED
        self.joined_at = self.chat.created_at if accept else None
        self.left_at = None if accept else timezone.now()
        self.save(update_fields=['status', 'joined_at', 'left_at', 'updated_at'])
        self.chat._emit_state_changed([self.user_id])
        return self

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


class Submission(models.Model):
    chat = models.OneToOneField(Chat, on_delete=models.CASCADE, related_name='submission_record')
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name='authored_submissions')
    recipient = models.ForeignKey(User, on_delete=models.PROTECT, related_name='received_submissions')
    client_draft_id = models.CharField(max_length=64, unique=True)
    status = models.IntegerField(choices=SubmissionStatusChoice.to_choices(), default=SubmissionStatusChoice.DRAFT, db_index=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    published_statement = models.ForeignKey('Square.Statement', on_delete=models.SET_NULL, null=True, blank=True, related_name='source_submissions')
    created_at = models.DateTimeField(auto_now_add=True)

    STATUS_NAMES = {
        SubmissionStatusChoice.DRAFT: 'draft',
        SubmissionStatusChoice.REVIEW: 'review',
        SubmissionStatusChoice.REVISION: 'revision',
        SubmissionStatusChoice.TERMINATED: 'terminated',
        SubmissionStatusChoice.READY: 'ready',
        SubmissionStatusChoice.PUBLISHED: 'published',
    }

    def jsonl(self):
        return dict(
            author=self.author.tiny_json(),
            recipient=self.recipient.tiny_json(),
            status=self.STATUS_NAMES[self.status],
            submitted_at=self.submitted_at.timestamp() if self.submitted_at else None,
            published_statement_id=self.published_statement_id,
        )

    def role_for(self, user: User):
        if user.id == self.author_id:
            return 'author'
        if user.id == self.recipient_id:
            return 'reviewer'
        return 'member'

    def can_send(self, user: User):
        return (
            user.id == self.author_id and self.status in (SubmissionStatusChoice.DRAFT, SubmissionStatusChoice.REVISION)
        ) or (
            user.id == self.recipient_id and self.status == SubmissionStatusChoice.REVIEW
        )

    def require_send_allowed(self, user: User):
        if not self.can_send(user):
            raise ChatErrors.SUBMISSION_SEND_FORBIDDEN

    def submit(self, user: User):
        if user.id != self.author_id or self.status not in (SubmissionStatusChoice.DRAFT, SubmissionStatusChoice.REVISION):
            raise ChatErrors.SUBMISSION_TRANSITION_FORBIDDEN
        from Message.models import Message, MessageTypeChoice
        if not Message.objects.filter(chat=self.chat, is_deleted=False).exclude(type=MessageTypeChoice.SYSTEM).exists():
            raise ChatErrors.SUBMISSION_EMPTY
        self.status = SubmissionStatusChoice.REVIEW
        self.submitted_at = timezone.now()
        self.save(update_fields=['status', 'submitted_at'])
        self.chat._emit_state_changed()
        return self

    def review(self, user: User, action: str):
        if user.id != self.recipient_id or self.status != SubmissionStatusChoice.REVIEW:
            raise ChatErrors.SUBMISSION_TRANSITION_FORBIDDEN
        next_status = {
            'revision': SubmissionStatusChoice.REVISION,
            'terminate': SubmissionStatusChoice.TERMINATED,
            'ready': SubmissionStatusChoice.READY,
        }.get(action)
        if next_status is None:
            raise ChatErrors.SUBMISSION_TRANSITION_FORBIDDEN
        from Message.models import Message
        with transaction.atomic():
            self.status = next_status
            self.save(update_fields=['status'])
            direct_chat = Chat.get_or_create_direct(user, self.author)
            Message.create_system(
                direct_chat,
                user,
                f'submission_{action}',
                submission_title=self.chat.title,
                submission_chat_id=self.chat_id,
            )
            self.chat._emit_state_changed()
        return self


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
    statement_reminder_enabled = models.BooleanField(default=False)
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
    def update(cls, chat: Chat, user: User, pinned=None, online_reminder_enabled=None, statement_reminder_enabled=None, notifications_muted=None, unread_badge_muted=None):
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
        if statement_reminder_enabled is not None:
            if chat.group and bool(statement_reminder_enabled):
                raise ChatErrors.NOT_DIRECT_CHAT(chat=chat.id)
            preference.statement_reminder_enabled = bool(statement_reminder_enabled)
            updates.append('statement_reminder_enabled')
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
        return self.dictify('pinned', 'online_reminder_enabled', 'statement_reminder_enabled', 'notifications_muted', 'unread_badge_muted')

    @classmethod
    def emit_peer_statement_events(cls, statement):
        from User.models import NotificationEvent

        preferences = cls.objects.filter(
            statement_reminder_enabled=True,
            chat__chat_type=ChatTypeChoice.DIRECT,
            chat__is_deleted=False,
            chat__chat_members__user=statement.user,
            chat__chat_members__status=ChatMemberStatusChoice.ACTIVE,
            user__is_deleted=False,
        ).select_related('chat', 'user').distinct()
        for preference in preferences:
            if preference.user_id == statement.user_id or not preference.chat.has_active_member(preference.user):
                continue
            NotificationEvent.emit_system_event(
                user=preference.user,
                actor=statement.user,
                payload=dict(kind='friend_statement', statement_id=statement.id),
            )

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
