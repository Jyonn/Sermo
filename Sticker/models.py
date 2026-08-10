import hashlib
from urllib.parse import urlparse

import requests
from django.db import IntegrityError, models, transaction
from django.http import HttpRequest

from User.models import User
from utils.qiniu import avatar_uri_for_key, sign_private_download_url


class StickerAsset(models.Model):
    content_hash = models.CharField(max_length=64, unique=True, db_index=True)
    storage_key = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True, default='image/png')
    file_size = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def index(cls, asset_id):
        asset = cls.objects.filter(id=asset_id).first()
        if asset is None:
            from Sticker.validators import StickerErrors
            raise StickerErrors.NOT_FOUND
        return asset

    @classmethod
    def from_source_uri(cls, source_uri: str, mime_type: str = ''):
        response = requests.get(sign_private_download_url(source_uri), stream=True, timeout=20)
        response.raise_for_status()
        digest = hashlib.sha256()
        file_size = 0
        for chunk in response.iter_content(chunk_size=128 * 1024):
            if not chunk:
                continue
            file_size += len(chunk)
            digest.update(chunk)
        content_hash = digest.hexdigest()
        storage_key = urlparse(source_uri).path.lstrip('/')
        defaults = dict(
            storage_key=storage_key,
            mime_type=(mime_type or response.headers.get('Content-Type') or 'image/png')[:100],
            file_size=file_size,
        )
        try:
            with transaction.atomic():
                return cls.objects.create(content_hash=content_hash, **defaults), True
        except IntegrityError:
            return cls.objects.get(content_hash=content_hash), False

    def source_uri(self):
        return avatar_uri_for_key(self.storage_key)

    def resource_uri(self, request: HttpRequest = None):
        path = f'/stickers/assets/{self.id}'
        return request.build_absolute_uri(path) if request is not None else path

    def jsonl(self, request: HttpRequest = None):
        return dict(
            sticker_asset_id=self.id,
            content_hash=self.content_hash,
            uri=self.resource_uri(request=request),
            mime_type=self.mime_type,
            file_size=self.file_size,
        )


class UserSticker(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stickers')
    asset = models.ForeignKey(StickerAsset, on_delete=models.PROTECT, related_name='owners')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'asset'], name='sticker_unique_user_asset'),
        ]

    @classmethod
    def index(cls, sticker_id):
        row = cls.objects.filter(id=sticker_id).first()
        if row is None:
            from Sticker.validators import StickerErrors
            raise StickerErrors.NOT_FOUND
        return row

    @classmethod
    def collect(cls, user: User, asset: StickerAsset):
        return cls.objects.get_or_create(user=user, asset=asset)

    def jsonl(self, request: HttpRequest = None):
        payload = self.asset.jsonl(request=request)
        payload.update(sticker_id=self.id, created_at=self.created_at.timestamp())
        return payload
