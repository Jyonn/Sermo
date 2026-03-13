from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from smartdjango import models, Choice

from Friendship.validators import FriendshipValidator, FriendshipErrors
from User.models import User


class FriendshipStatusChoice(Choice):
    PENDING = 0
    ACCEPTED = 1
    REJECTED = 2
    DELETED = 3


class Friendship(models.Model):
    vldt = FriendshipValidator

    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='friendships', db_index=True)
    user_low = models.ForeignKey(User, on_delete=models.CASCADE, related_name='friendships_as_low')
    user_high = models.ForeignKey(User, on_delete=models.CASCADE, related_name='friendships_as_high')
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='requested_friendships',
    )
    status = models.IntegerField(
        choices=FriendshipStatusChoice.to_choices(),
        default=FriendshipStatusChoice.PENDING,
        db_index=True,
    )
    is_system_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['space', 'user_low', 'user_high'], name='unique_friendship_pair'),
        ]

    @classmethod
    def index(cls, friendship_id):
        try:
            return cls.objects.get(id=friendship_id)
        except cls.DoesNotExist:
            raise FriendshipErrors.NOT_EXISTS(attr='id', value=friendship_id)

    @classmethod
    def _pair(cls, user_a: User, user_b: User):
        if user_a.id == user_b.id:
            raise FriendshipErrors.INVALID_TARGET
        if user_a.space_id != user_b.space_id:
            raise FriendshipErrors.UNALIGNED_SPACE
        if user_a.id < user_b.id:
            return user_a.space, user_a, user_b
        return user_a.space, user_b, user_a

    @classmethod
    def between(cls, user_a: User, user_b: User):
        space, user_low, user_high = cls._pair(user_a, user_b)
        return cls.objects.filter(space=space, user_low=user_low, user_high=user_high).first()

    def _is_participant(self, user: User):
        return user.id in (self.user_low_id, self.user_high_id)

    def _request_target(self):
        if self.requested_by_id is None:
            return self.user_low
        if self.requested_by_id == self.user_low_id:
            return self.user_high
        return self.user_low

    @classmethod
    def ensure_locked_friendship(cls, user_a: User, user_b: User):
        space, user_low, user_high = cls._pair(user_a, user_b)
        item, _created = cls.objects.get_or_create(
            space=space,
            user_low=user_low,
            user_high=user_high,
            defaults=dict(
                requested_by=user_low,
                status=FriendshipStatusChoice.ACCEPTED,
                is_system_locked=True,
                responded_at=timezone.now(),
            ),
        )
        if item.status != FriendshipStatusChoice.ACCEPTED or not item.is_system_locked:
            item.status = FriendshipStatusChoice.ACCEPTED
            item.is_system_locked = True
            item.responded_at = timezone.now()
            item.save(update_fields=['status', 'is_system_locked', 'responded_at', 'updated_at'])
        return item

    @classmethod
    def create_request(cls, from_user: User, to_user: User):
        if not from_user.verified:
            raise FriendshipErrors.REQUEST_FORBIDDEN

        space, user_low, user_high = cls._pair(from_user, to_user)
        item = cls.objects.filter(space=space, user_low=user_low, user_high=user_high).first()

        if item is None:
            item = cls.objects.create(
                space=space,
                user_low=user_low,
                user_high=user_high,
                requested_by=from_user,
                status=FriendshipStatusChoice.PENDING,
            )
        else:
            if item.status == FriendshipStatusChoice.ACCEPTED:
                raise FriendshipErrors.ALREADY_FRIENDS
            if item.status == FriendshipStatusChoice.PENDING:
                raise FriendshipErrors.REQUEST_EXISTS
            item.requested_by = from_user
            item.status = FriendshipStatusChoice.PENDING
            item.responded_at = None
            item.save(update_fields=['requested_by', 'status', 'responded_at', 'updated_at'])

        from User.models import NotificationEvent
        NotificationEvent.emit_system_event(
            user=to_user,
            actor=from_user,
            payload=dict(
                kind='friend_request',
                request_id=item.id,
                from_user=from_user.tiny_json(),
            ),
        )
        return item

    def accept(self, user: User):
        if not self._is_participant(user):
            raise FriendshipErrors.REQUEST_FORBIDDEN
        if self.status != FriendshipStatusChoice.PENDING:
            raise FriendshipErrors.REQUEST_CLOSED
        if self.requested_by_id == user.id:
            raise FriendshipErrors.REQUEST_FORBIDDEN

        with transaction.atomic():
            self.status = FriendshipStatusChoice.ACCEPTED
            self.responded_at = timezone.now()
            self.save(update_fields=['status', 'responded_at', 'updated_at'])

            from User.models import NotificationEvent
            if self.requested_by_id:
                NotificationEvent.emit_system_event(
                    user=self.requested_by,
                    actor=user,
                    payload=dict(
                        kind='friend_request_accepted',
                        request_id=self.id,
                        by_user=user.tiny_json(),
                    ),
                )
        return self

    def reject(self, user: User):
        if not self._is_participant(user):
            raise FriendshipErrors.REQUEST_FORBIDDEN
        if self.status != FriendshipStatusChoice.PENDING:
            raise FriendshipErrors.REQUEST_CLOSED
        if self.requested_by_id == user.id:
            raise FriendshipErrors.REQUEST_FORBIDDEN

        self.status = FriendshipStatusChoice.REJECTED
        self.responded_at = timezone.now()
        self.save(update_fields=['status', 'responded_at', 'updated_at'])
        return self

    def remove(self, user: User):
        if not self._is_participant(user):
            raise FriendshipErrors.REQUEST_FORBIDDEN
        if self.is_system_locked:
            raise FriendshipErrors.LOCKED_FORBIDDEN
        if self.status != FriendshipStatusChoice.ACCEPTED:
            raise FriendshipErrors.NOT_FRIENDS

        self.status = FriendshipStatusChoice.DELETED
        self.responded_at = timezone.now()
        self.save(update_fields=['status', 'responded_at', 'updated_at'])
        return self

    @classmethod
    def friends_of(cls, user: User):
        relations = cls.objects.filter(
            space=user.space,
            status=FriendshipStatusChoice.ACCEPTED,
        ).filter(Q(user_low=user) | Q(user_high=user))
        friends = []
        for relation in relations.select_related('user_low', 'user_high'):
            friend = relation.user_high if relation.user_low_id == user.id else relation.user_low
            if not friend.is_deleted:
                friends.append(friend)
        return friends

    @classmethod
    def pending_incoming(cls, user: User):
        rows = cls.objects.filter(
            space=user.space,
            status=FriendshipStatusChoice.PENDING,
        ).filter(
            (Q(user_low=user) | Q(user_high=user)) &
            ~Q(requested_by=user)
        ).order_by('-updated_at')
        return list(rows.select_related('user_low', 'user_high', 'requested_by')[:100])

    @classmethod
    def pending_outgoing(cls, user: User):
        rows = cls.objects.filter(
            space=user.space,
            status=FriendshipStatusChoice.PENDING,
            requested_by=user,
        ).order_by('-updated_at')
        return list(rows.select_related('user_low', 'user_high', 'requested_by')[:100])

    def _dictify_created_at(self):
        return self.created_at.timestamp()

    def _dictify_updated_at(self):
        return self.updated_at.timestamp()

    def _dictify_responded_at(self):
        if self.responded_at is None:
            return None
        return self.responded_at.timestamp()

    def _dictify_from_user(self):
        if self.requested_by_id is None:
            return self.user_low.tiny_json()
        return self.requested_by.tiny_json()

    def _dictify_to_user(self):
        return self._request_target().tiny_json()

    def json(self):
        return self.dictify(
            'id->request_id',
            'status',
            'is_system_locked',
            'from_user',
            'to_user',
            'created_at',
            'updated_at',
            'responded_at',
        )
