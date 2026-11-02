"""Account-isolated APNs registrations; no provider delivery is enabled here."""
import datetime
import hashlib
import re
import uuid

from django.db import models, transaction
from django.utils import timezone


class NativePushConflict(ValueError):
    pass


class NativePushBinding(models.Model):
    LEASE_DAYS = 45
    MAX_REVISION = 2 ** 63 - 1
    ENVIRONMENTS = (('sandbox', 'sandbox'), ('production', 'production'))

    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='native_push_bindings')
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='native_push_bindings')
    installation_id = models.UUIDField()
    environment = models.CharField(max_length=10, choices=ENVIRONMENTS)
    token = models.CharField(max_length=1024, blank=True, default='')
    token_digest = models.CharField(max_length=64, blank=True, default='')
    opted_in = models.BooleanField(default=False)
    revision = models.PositiveBigIntegerField(default=1)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(
            fields=['user', 'installation_id', 'environment'], name='unique_native_push_account_install',
        )]

    @classmethod
    def _identity(cls, installation_id, environment, expected_revision):
        value = str(installation_id)
        try:
            installation = uuid.UUID(value)
        except (ValueError, AttributeError, TypeError):
            raise ValueError('invalid installation') from None
        if value.lower() != str(installation) or installation.version != 4:
            raise ValueError('invalid installation')
        if environment not in dict(cls.ENVIRONMENTS):
            raise ValueError('invalid environment')
        if type(expected_revision) is not int or not 0 <= expected_revision < cls.MAX_REVISION:
            raise ValueError('invalid revision')
        return installation

    @staticmethod
    def _token(value):
        # APNs tokens have variable length; this bound limits our request/storage size.
        if not isinstance(value, str) or not 2 <= len(value) <= 1024 or len(value) % 2 or not re.fullmatch(r'[0-9a-fA-F]+', value):
            raise ValueError('invalid token')
        value = value.lower()
        return value, hashlib.sha256(value.encode('ascii')).hexdigest()

    @classmethod
    def set_preference(cls, user, installation_id, environment, *, opted_in, expected_revision, token=None):
        if type(opted_in) is not bool:
            raise ValueError('invalid preference')
        return cls._write(user, installation_id, environment, expected_revision, token, opted_in)

    @classmethod
    def refresh_registration(cls, user, installation_id, environment, *, token, expected_revision):
        return cls._write(user, installation_id, environment, expected_revision, token, None)

    @classmethod
    def _write(cls, user, installation_id, environment, expected_revision, token, preference):
        from User.models import User

        installation = cls._identity(installation_id, environment, expected_revision)
        normalized = cls._token(token) if token is not None else None
        if preference is None and normalized is None:
            raise ValueError('token required')
        with transaction.atomic():
            owner = User.objects.select_for_update().get(pk=user.pk)
            binding = cls.objects.select_for_update().filter(user=owner, installation_id=installation, environment=environment).first()
            if (binding.revision if binding else 0) != expected_revision:
                raise NativePushConflict('registration changed')
            # Background token updates never invent consent or resurrect an absent binding.
            if binding is None and preference is None:
                return None
            if binding is None:
                binding = cls(user=owner, space_id=owner.space_id, installation_id=installation, environment=environment, revision=0)
            if binding.space_id != owner.space_id:
                raise NativePushConflict('account space changed')
            if normalized is not None:
                value, digest = normalized
                if binding.token_digest != digest:
                    binding.invalidated_at = None
                binding.token, binding.token_digest = value, digest
            if preference is not None:
                binding.opted_in = preference
            if binding.opted_in and not binding.token:
                raise ValueError('token required')
            binding.revision += 1
            binding.save()
            return binding

    @classmethod
    def active_for_user(cls, user):
        cutoff = timezone.now() - datetime.timedelta(days=cls.LEASE_DAYS)
        return cls.objects.filter(user=user, space_id=user.space_id, opted_in=True,
                                  invalidated_at__isnull=True, last_seen_at__gte=cutoff).exclude(token='')

    @classmethod
    def invalidate_token(cls, binding_id, token_digest, revision):
        if type(revision) is not int or not 0 < revision < cls.MAX_REVISION:
            raise ValueError('invalid revision')
        return cls.objects.filter(pk=binding_id, token_digest=token_digest, revision=revision).update(
            invalidated_at=timezone.now(), revision=models.F('revision') + 1,
        )

    def public_status(self):
        return dict(installation_id=str(self.installation_id), environment=self.environment,
                    opted_in=self.opted_in, revision=self.revision,
                    registered=bool(self.token) and self.invalidated_at is None)
