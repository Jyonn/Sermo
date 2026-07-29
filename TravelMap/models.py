from django.db import transaction
from django.utils import timezone
from smartdjango import models

from TravelMap.validators import TravelMapErrors, TravelMapValidator
from User.models import User
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice


class MapCheckIn(models.Model):
    validators = TravelMapValidator
    vldt = TravelMapValidator

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='map_checkins')
    region_code = models.CharField(max_length=vldt.REGION_CODE_MAX_LENGTH)
    region_name = models.CharField(max_length=vldt.REGION_NAME_MAX_LENGTH)
    country_code = models.CharField(max_length=vldt.COUNTRY_CODE_MAX_LENGTH, db_index=True)
    country_name = models.CharField(max_length=vldt.COUNTRY_NAME_MAX_LENGTH)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    accuracy_meters = models.FloatField(null=True, blank=True)
    geocoding_provider = models.CharField(max_length=24, null=True, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['country_code', 'region_name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'region_code'], name='travel_map_unique_user_region'),
        ]

    @classmethod
    def check_in(
        cls,
        user,
        region_code,
        region_name,
        country_code,
        country_name,
        latitude,
        longitude,
        accuracy_meters,
        geocoding_provider,
    ):
        normalized_region_code = (region_code or '').strip()[:cls.vldt.REGION_CODE_MAX_LENGTH]
        normalized_region_name = (region_name or '').strip()[:cls.vldt.REGION_NAME_MAX_LENGTH]
        normalized_country_code = (country_code or '').strip().upper()[:cls.vldt.COUNTRY_CODE_MAX_LENGTH]
        normalized_country_name = (country_name or '').strip()[:cls.vldt.COUNTRY_NAME_MAX_LENGTH]
        if not all((normalized_region_code, normalized_region_name, normalized_country_code, normalized_country_name)):
            raise TravelMapErrors.REGION_INVALID
        item, _ = cls.objects.get_or_create(
            user=user,
            region_code=normalized_region_code,
            defaults=dict(
                region_name=normalized_region_name,
                country_code=normalized_country_code,
                country_name=normalized_country_name,
                latitude=latitude,
                longitude=longitude,
                accuracy_meters=accuracy_meters,
                geocoding_provider=geocoding_provider,
            ),
        )
        return item

    def json(self):
        return dict(
            region_code=self.region_code,
            region_name=self.region_name,
            country_code=self.country_code,
            country_name=self.country_name,
            checked_at=self.checked_at.timestamp(),
        )


class MapChatGrant(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='map_grants')
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chat_map_grants')
    active = models.BooleanField(default=True, db_index=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['chat', 'owner'], name='travel_map_unique_chat_owner'),
        ]

    @classmethod
    def _require_member(cls, chat, user):
        if not chat.has_active_member(user):
            raise TravelMapErrors.CHAT_ACCESS_DENIED

    @classmethod
    def grant(cls, chat, owner):
        cls._require_member(chat, owner)
        item, _ = cls.objects.update_or_create(
            chat=chat,
            owner=owner,
            defaults=dict(active=True, revoked_at=None),
        )
        return item

    @classmethod
    def revoke(cls, chat, owner):
        cls._require_member(chat, owner)
        cls.objects.filter(chat=chat, owner=owner, active=True).update(
            active=False,
            revoked_at=timezone.now(),
        )

    @classmethod
    def status(cls, chat, current_user):
        cls._require_member(chat, current_user)
        active_owner_ids = set(cls.objects.filter(chat=chat, active=True).values_list('owner_id', flat=True))
        members = ChatMember.objects.filter(
            chat=chat,
            status=ChatMemberStatusChoice.ACTIVE,
            user__is_deleted=False,
        ).select_related('user')
        shared_members = [member.user.tiny_json() for member in members if member.user_id in active_owner_ids]
        return dict(
            authorized_by_me=current_user.id in active_owner_ids,
            shared_members=shared_members,
        )

    @classmethod
    def maps(cls, chat, current_user):
        status = cls.status(chat, current_user)
        if not status['authorized_by_me']:
            raise TravelMapErrors.ACCESS_DENIED
        owner_ids = [item['user_id'] for item in status['shared_members']]
        rows = MapCheckIn.objects.filter(user_id__in=owner_ids).select_related('user')
        regions_by_user = {owner_id: [] for owner_id in owner_ids}
        for row in rows:
            regions_by_user.setdefault(row.user_id, []).append(row.json())
        return dict(
            chat_id=chat.id,
            authorized_by_me=status['authorized_by_me'],
            maps=[
                dict(owner=member, regions=regions_by_user.get(member['user_id'], []))
                for member in status['shared_members']
            ],
        )


class MapAccessGrant(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='map_grants_given')
    viewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='map_grants_received')
    active = models.BooleanField(default=True, db_index=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'viewer'], name='travel_map_unique_owner_viewer'),
        ]

    @classmethod
    def _validate_pair(cls, owner, viewer):
        if owner.id == viewer.id:
            raise TravelMapErrors.SAME_USER
        if owner.space_id != viewer.space_id:
            raise TravelMapErrors.SPACE_MISMATCH

    @classmethod
    def grant(cls, owner, viewer):
        cls._validate_pair(owner, viewer)
        item, _ = cls.objects.update_or_create(
            owner=owner,
            viewer=viewer,
            defaults=dict(active=True, revoked_at=None),
        )
        return item

    @classmethod
    def revoke(cls, owner, viewer):
        cls._validate_pair(owner, viewer)
        cls.objects.filter(owner=owner, viewer=viewer, active=True).update(
            active=False,
            revoked_at=timezone.now(),
        )

    @classmethod
    def has_access(cls, owner, viewer):
        if owner.id == viewer.id:
            return True
        return cls.objects.filter(owner=owner, viewer=viewer, active=True).exists()

    @classmethod
    def reciprocate(cls, current_user, original_owner):
        cls._validate_pair(current_user, original_owner)
        if not cls.has_access(original_owner, current_user):
            raise TravelMapErrors.RECIPROCAL_GRANT_DENIED
        return cls.grant(current_user, original_owner)

    @classmethod
    def status_between(cls, current_user, other_user):
        return dict(
            can_view_theirs=cls.has_access(other_user, current_user),
            they_can_view_mine=cls.has_access(current_user, other_user),
        )


class TravelMap:
    @classmethod
    def payload(cls, owner, viewer):
        if not MapAccessGrant.has_access(owner, viewer):
            raise TravelMapErrors.ACCESS_DENIED
        regions = MapCheckIn.objects.filter(user=owner)
        return dict(
            owner=owner.tiny_json(),
            regions=[item.json() for item in regions],
            access=MapAccessGrant.status_between(viewer, owner),
        )

    @classmethod
    def comparison_payload(cls, current_user, other_user):
        if not MapAccessGrant.has_access(other_user, current_user):
            raise TravelMapErrors.ACCESS_DENIED
        with transaction.atomic():
            mine = list(MapCheckIn.objects.filter(user=current_user))
            theirs = list(MapCheckIn.objects.filter(user=other_user))
        return dict(
            me=current_user.tiny_json(),
            other=other_user.tiny_json(),
            my_regions=[item.json() for item in mine],
            other_regions=[item.json() for item in theirs],
            access=MapAccessGrant.status_between(current_user, other_user),
        )
