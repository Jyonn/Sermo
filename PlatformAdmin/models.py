import hashlib
import hmac
import secrets
from base64 import b32decode, b32encode

from django.db import models
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
