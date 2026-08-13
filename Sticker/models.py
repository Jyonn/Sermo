import hashlib
import threading
from urllib.parse import urlparse

import requests
from django.db import IntegrityError, close_old_connections, models, transaction
from django.http import HttpRequest
from django.utils import timezone

from User.models import User
from utils.qiniu import avatar_uri_for_key, sign_private_download_url


class StickerAsset(models.Model):
    _DIMENSION_FETCHING_IDS = set()
    _DIMENSION_FETCHING_LOCK = threading.Lock()

    content_hash = models.CharField(max_length=64, unique=True, db_index=True)
    storage_key = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100, blank=True, default='image/png')
    file_size = models.PositiveIntegerField(default=0)
    pixel_width = models.PositiveIntegerField(null=True, blank=True)
    pixel_height = models.PositiveIntegerField(null=True, blank=True)
    dimensions_checked_at = models.DateTimeField(null=True, blank=True)
    dimensions_error = models.CharField(max_length=500, blank=True, default='')
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
                asset = cls.objects.create(content_hash=content_hash, **defaults)
                transaction.on_commit(lambda: cls.fetch_dimensions_async(asset.id))
                return asset, True
        except IntegrityError:
            asset = cls.objects.get(content_hash=content_hash)
            asset.queue_missing_dimensions()
            return asset, False

    def queue_missing_dimensions(self):
        if self.pixel_width and self.pixel_height:
            return
        transaction.on_commit(lambda: self.fetch_dimensions_async(self.id))

    @classmethod
    def fetch_dimensions_async(cls, asset_id):
        with cls._DIMENSION_FETCHING_LOCK:
            if asset_id in cls._DIMENSION_FETCHING_IDS:
                return
            cls._DIMENSION_FETCHING_IDS.add(asset_id)
        threading.Thread(target=cls.refresh_dimensions_by_id, args=(asset_id,), daemon=True).start()

    @classmethod
    def refresh_dimensions_by_id(cls, asset_id):
        close_old_connections()
        try:
            asset = cls.objects.filter(id=asset_id).first()
            if asset is not None:
                cls.refresh_dimensions(asset)
        finally:
            with cls._DIMENSION_FETCHING_LOCK:
                cls._DIMENSION_FETCHING_IDS.discard(asset_id)
            close_old_connections()

    @classmethod
    def refresh_dimensions(cls, asset):
        from Message.image_metadata import fetch_qiniu_image_info, parse_image_info

        try:
            properties = parse_image_info(fetch_qiniu_image_info(asset.source_uri()))
            asset.file_size = properties['file_size']
            asset.pixel_width = properties['pixel_width']
            asset.pixel_height = properties['pixel_height']
            asset.dimensions_error = ''
        except Exception as error:
            asset.dimensions_error = str(error)[:500]
        asset.dimensions_checked_at = timezone.now()
        asset.save(update_fields=[
            'file_size', 'pixel_width', 'pixel_height',
            'dimensions_checked_at', 'dimensions_error',
        ])
        return asset

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
            pixel_width=self.pixel_width,
            pixel_height=self.pixel_height,
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
        sticker, created = cls.objects.get_or_create(user=user, asset=asset)
        if created:
            user.award_growth('explore:sticker_collect')
        return sticker, created

    def jsonl(self, request: HttpRequest = None):
        payload = self.asset.jsonl(request=request)
        payload.update(sticker_id=self.id, created_at=self.created_at.timestamp())
        return payload
