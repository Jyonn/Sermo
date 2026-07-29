from django.db import transaction
from django.utils import timezone
from smartdjango import models

from TravelMap.validators import TravelMapErrors, TravelMapValidator
from User.models import User


class MapCheckIn(models.Model):
    validators = TravelMapValidator
    vldt = TravelMapValidator

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='map_checkins')
    region_code = models.CharField(max_length=vldt.REGION_CODE_MAX_LENGTH)
    region_name = models.CharField(max_length=vldt.REGION_NAME_MAX_LENGTH)
    country_code = models.CharField(max_length=vldt.COUNTRY_CODE_MAX_LENGTH, db_index=True)
    country_name = models.CharField(max_length=vldt.COUNTRY_NAME_MAX_LENGTH)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['country_code', 'region_name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'region_code'], name='travel_map_unique_user_region'),
        ]

    @classmethod
    def set_checked(cls, user, region_code, region_name, country_code, country_name, checked):
        normalized_region_code = (region_code or '').strip()[:cls.vldt.REGION_CODE_MAX_LENGTH]
        normalized_region_name = (region_name or '').strip()[:cls.vldt.REGION_NAME_MAX_LENGTH]
        normalized_country_code = (country_code or '').strip().upper()[:cls.vldt.COUNTRY_CODE_MAX_LENGTH]
        normalized_country_name = (country_name or '').strip()[:cls.vldt.COUNTRY_NAME_MAX_LENGTH]
        if not all((normalized_region_code, normalized_region_name, normalized_country_code, normalized_country_name)):
            raise TravelMapErrors.REGION_INVALID
        if checked:
            item, _ = cls.objects.update_or_create(
                user=user,
                region_code=normalized_region_code,
                defaults=dict(
                    region_name=normalized_region_name,
                    country_code=normalized_country_code,
                    country_name=normalized_country_name,
                ),
            )
            return item
        cls.objects.filter(user=user, region_code=normalized_region_code).delete()
        return None

    def json(self):
        return dict(
            region_code=self.region_code,
            region_name=self.region_name,
            country_code=self.country_code,
            country_name=self.country_name,
            checked_at=self.checked_at.timestamp(),
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
