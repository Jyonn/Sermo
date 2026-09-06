import hashlib
import hmac
import secrets
from base64 import b32decode, b32encode

from django.db import models
from django.db import transaction
from django.utils import timezone


class PlatformAdminSecurity(models.Model):
    singleton_key = models.CharField(max_length=16, unique=True, default='primary')
    totp_secret = models.CharField(max_length=64, blank=True, default='')
    mfa_enabled = models.BooleanField(default=False)
    recovery_code_hashes = models.JSONField(default=list, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def primary(cls):
        value, _ = cls.objects.get_or_create(singleton_key='primary')
        return value

    @staticmethod
    def new_secret():
        return b32encode(secrets.token_bytes(20)).decode().rstrip('=')

    @staticmethod
    def verify_totp(secret, code, at=None):
        normalized = ''.join(character for character in str(code or '') if character.isdigit())
        if len(normalized) != 6 or not secret:
            return False
        timestamp = int((at or timezone.now()).timestamp())
        padded = secret + '=' * ((8 - len(secret) % 8) % 8)
        key = b32decode(padded, casefold=True)
        for offset in (-1, 0, 1):
            counter = (timestamp // 30) + offset
            digest = hmac.new(key, counter.to_bytes(8, 'big'), hashlib.sha1).digest()
            index = digest[-1] & 0x0F
            value = (int.from_bytes(digest[index:index + 4], 'big') & 0x7FFFFFFF) % 1_000_000
            if hmac.compare_digest(f'{value:06d}', normalized):
                return True
        return False

    @staticmethod
    def hash_recovery_code(code):
        return hashlib.sha256(str(code).strip().upper().encode()).hexdigest()


class PlatformAdminEmailCode(models.Model):
    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class PlatformAuditLog(models.Model):
    action = models.CharField(max_length=64, db_index=True)
    target_type = models.CharField(max_length=32, blank=True, default='')
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    summary = models.CharField(max_length=255, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-id']


class PlatformAdminEmailReviewState(models.Model):
    CAPTURE_LIMIT = 20

    singleton_key = models.CharField(max_length=16, unique=True, default='primary')
    enabled = models.BooleanField(default=False)
    captured_count = models.PositiveSmallIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def primary(cls):
        value, _ = cls.objects.get_or_create(singleton_key='primary')
        return value

    @classmethod
    def start(cls):
        with transaction.atomic():
            state = cls.primary()
            state = cls.objects.select_for_update().get(id=state.id)
            state.records.all().delete()
            state.enabled = True
            state.captured_count = 0
            state.started_at = timezone.now()
            state.completed_at = None
            state.save(update_fields=['enabled', 'captured_count', 'started_at', 'completed_at', 'updated_at'])
        return state

    @classmethod
    def stop(cls):
        with transaction.atomic():
            state = cls.primary()
            state = cls.objects.select_for_update().get(id=state.id)
            if state.enabled:
                state.enabled = False
                state.completed_at = timezone.now()
                state.save(update_fields=['enabled', 'completed_at', 'updated_at'])
        return state

    @classmethod
    def claim_email(cls, **payload):
        with transaction.atomic():
            state = cls.primary()
            state = cls.objects.select_for_update().get(id=state.id)
            if not state.enabled or state.captured_count >= cls.CAPTURE_LIMIT:
                return None
            sequence = state.captured_count + 1
            record = state.records.create(sequence=sequence, **payload)
            state.captured_count = sequence
            if sequence >= cls.CAPTURE_LIMIT:
                state.enabled = False
                state.completed_at = timezone.now()
                update_fields = ['captured_count', 'enabled', 'completed_at', 'updated_at']
            else:
                update_fields = ['captured_count', 'updated_at']
            state.save(update_fields=update_fields)
            return record

    def json(self):
        return dict(
            enabled=self.enabled,
            captured_count=self.captured_count,
            limit=self.CAPTURE_LIMIT,
            remaining=max(0, self.CAPTURE_LIMIT - self.captured_count),
            started_at=self.started_at.timestamp() if self.started_at else None,
            completed_at=self.completed_at.timestamp() if self.completed_at else None,
        )


class PlatformAdminEmailReviewRecord(models.Model):
    STATUS_PROCESSING = 'processing'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PROCESSING, STATUS_PROCESSING),
        (STATUS_SENT, STATUS_SENT),
        (STATUS_FAILED, STATUS_FAILED),
    )

    state = models.ForeignKey(
        PlatformAdminEmailReviewState,
        on_delete=models.CASCADE,
        related_name='records',
    )
    sequence = models.PositiveSmallIntegerField()
    recipient = models.TextField(blank=True, default='')
    mail_format = models.CharField(max_length=32, blank=True, default='')
    title = models.TextField(blank=True, default='')
    body = models.JSONField(null=True, blank=True)
    body_text = models.TextField(blank=True, default='')
    locale = models.CharField(max_length=32, blank=True, default='')
    recipient_name = models.CharField(max_length=255, blank=True, default='')
    action_url = models.TextField(blank=True, default='')
    footer_note = models.TextField(blank=True, default='')
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PROCESSING)
    detail = models.TextField(blank=True, default='')
    request_id = models.CharField(max_length=255, blank=True, default='')
    provider_response = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-sequence']
        constraints = [models.UniqueConstraint(
            fields=['state', 'sequence'],
            name='unique_platform_email_review_sequence',
        )]

    def summary_json(self):
        return dict(
            record_id=self.id,
            sequence=self.sequence,
            recipient=self.recipient,
            title=self.title,
            mail_format=self.mail_format,
            locale=self.locale,
            status=self.status,
            detail=self.detail,
            created_at=self.created_at.timestamp(),
            completed_at=self.completed_at.timestamp() if self.completed_at else None,
        )

    def detail_json(self):
        return dict(
            **self.summary_json(),
            body=self.body,
            body_text=self.body_text,
            recipient_name=self.recipient_name,
            action_url=self.action_url,
            footer_note=self.footer_note,
            request_id=self.request_id,
            provider_response=self.provider_response,
        )
