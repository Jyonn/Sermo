"""Durable native delivery claims. No provider or scheduler is enabled here."""
import datetime
import uuid

from django.db import models, transaction
from django.utils import timezone

from User.native_push_models import NativePushBinding


class NativePushDelivery(models.Model):
    STATES = tuple((value, value) for value in ('pending', 'processing', 'sent', 'skipped', 'failed', 'unknown'))
    CLAIM_MINUTES = 15
    MAX_AGE_HOURS = 24
    event = models.ForeignKey('User.NotificationEvent', on_delete=models.CASCADE, related_name='native_deliveries')
    binding = models.ForeignKey('User.NativePushBinding', on_delete=models.CASCADE, related_name='deliveries')
    binding_revision = models.PositiveBigIntegerField()
    token_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=10, choices=STATES, default='pending', db_index=True)
    claim_id = models.UUIDField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=32, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['event', 'binding'], name='unique_native_push_event_binding')]

    @staticmethod
    def _environments(values):
        if not isinstance(values, (tuple, list, set, frozenset)) or any(
            not isinstance(value, str) or value not in dict(NativePushBinding.ENVIRONMENTS) for value in values
        ):
            raise ValueError('invalid provider environments')
        return set(values)

    @staticmethod
    def _event_allowed(event):
        from User.models import NotificationTopicPreference, NotificationRouteChannelChoice

        return (not event.user.is_deleted and event.space_id == event.user.space_id
                and NotificationTopicPreference.is_enabled_for_event(event, NotificationRouteChannelChoice.WEB))

    @classmethod
    def enqueue(cls, event, *, environments=()):
        environments = cls._environments(environments)
        if not environments or not cls._event_allowed(event):
            return []
        rows = []
        for binding in NativePushBinding.active_for_user(event.user).filter(environment__in=environments):
            row, created = cls.objects.get_or_create(event=event, binding=binding, defaults=dict(
                binding_revision=binding.revision, token_digest=binding.token_digest,
            ))
            if created:
                rows.append(row)
        return rows

    @classmethod
    def claim(cls, delivery_id, *, environments=()):
        environments = cls._environments(environments)
        if not environments:
            return None
        with transaction.atomic():
            row = cls.objects.select_for_update().select_related('event__user', 'binding').filter(pk=delivery_id).first()
            if row is None or row.state != 'pending' or row.binding.environment not in environments:
                return None
            now = timezone.now()
            reason = None
            if min(row.created_at, row.event.created_at) < now - datetime.timedelta(hours=cls.MAX_AGE_HOURS):
                reason = 'expired'
            elif not cls._event_allowed(row.event):
                reason = 'policy_disabled'
            elif not NativePushBinding.active_for_user(row.event.user).filter(
                pk=row.binding_id, revision=row.binding_revision, token_digest=row.token_digest,
            ).exists():
                reason = 'binding_changed'
            if reason:
                row.state, row.reason, row.finished_at = 'skipped', reason, now
                row.save(update_fields=['state', 'reason', 'finished_at'])
                return None
            row.state, row.claim_id, row.claimed_at = 'processing', uuid.uuid4(), now
            row.save(update_fields=['state', 'claim_id', 'claimed_at'])
            return row

    @classmethod
    def finish(cls, delivery_id, claim_id, outcome):
        # Never store provider exception text, device tokens or payloads in logs.
        if outcome not in ('sent', 'failed', 'unknown', 'rejected', 'skipped'):
            raise ValueError('invalid delivery outcome')
        if not isinstance(claim_id, uuid.UUID):
            raise ValueError('invalid claim')
        return cls.objects.filter(pk=delivery_id, state='processing', claim_id=claim_id).update(
            state='failed' if outcome == 'rejected' else outcome,
            reason='provider_rejected' if outcome == 'rejected' else '', finished_at=timezone.now(),
        )

    @classmethod
    def expire_claims(cls, *, environments=None):
        # A worker may have sent before crashing. Do not retry an unknown outcome.
        rows = cls.objects.filter(state='processing', claimed_at__lt=(
            timezone.now() - datetime.timedelta(minutes=cls.CLAIM_MINUTES)
        ))
        if environments is not None:
            rows = rows.filter(binding__environment__in=cls._environments(environments))
        return rows.update(state='unknown', reason='claim_expired', finished_at=timezone.now())
