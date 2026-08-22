import hashlib
import mimetypes

import requests
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from Message.models import (
    ForwardBundleItem,
    MediaAsset,
    MediaAssetAlias,
    MediaResource,
    Message,
)
from Square.models import StatementMedia
from utils.qiniu import delete_message_media_file, sign_private_download_url


class Command(BaseCommand):
    help = 'Backfill SHA-256 hashes and globally merge identical physical media assets.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    @staticmethod
    def _digest(asset):
        digest = hashlib.sha256()
        file_size = 0
        with requests.get(
            sign_private_download_url(asset.source_uri), stream=True, timeout=(10, 120),
        ) as response:
            response.raise_for_status()
            mime_type = str(response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    digest.update(chunk)
                    file_size += len(chunk)
        if not mime_type or mime_type == 'application/octet-stream':
            mime_type = mimetypes.guess_type(asset.source_key)[0] or mime_type
        return digest.hexdigest(), file_size, mime_type[:100]

    @staticmethod
    def _merge_resource(resource, canonical):
        target = MediaResource.objects.filter(
            owner_id=resource.owner_id,
            asset=canonical,
            kind=resource.kind,
            file_name=resource.file_name,
        ).first()
        if target is None:
            resource.asset = canonical
            resource.save(update_fields=['asset'])
            return
        Message.objects.filter(media_resource=resource).update(media_resource=target)
        ForwardBundleItem.objects.filter(media_resource=resource).update(media_resource=target)
        resource.delete()

    @classmethod
    def _merge_asset(cls, duplicate, canonical):
        old_key = duplicate.source_key
        with transaction.atomic():
            MediaAssetAlias.objects.get_or_create(
                slug=duplicate.blob_slug,
                defaults={'asset': canonical},
            )
            for resource in duplicate.resources.select_for_update().iterator(chunk_size=200):
                cls._merge_resource(resource, canonical)
            StatementMedia.objects.filter(media_asset=duplicate).update(media_asset=canonical)
            MediaAssetAlias.objects.filter(asset=duplicate).update(asset=canonical)
            duplicate.delete()
        try:
            delete_message_media_file(old_key)
        except Exception:
            # The database is authoritative; orphaned Qiniu objects can be retried later.
            pass

    def handle(self, *args, **options):
        queryset = MediaAsset.objects.order_by('id')
        if not options['force']:
            queryset = queryset.filter(Q(content_hash__isnull=True) | Q(content_hash=''))
        if options['limit'] > 0:
            queryset = queryset[:options['limit']]

        completed = merged = failed = 0
        for asset_id in list(queryset.values_list('id', flat=True)):
            try:
                asset = MediaAsset.objects.get(id=asset_id)
                content_hash, file_size, mime_type = self._digest(asset)
                duplicate = MediaAsset.find_duplicate(content_hash, file_size=file_size)
                hash_owner = MediaAsset.objects.filter(content_hash=content_hash).exclude(id=asset.id).first()
                if duplicate is None and hash_owner is not None and hash_owner.file_size is None:
                    owner_hash, owner_size, owner_mime = self._digest(hash_owner)
                    if owner_hash == content_hash:
                        hash_owner.file_size = owner_size
                        if not hash_owner.mime_type and owner_mime:
                            hash_owner.mime_type = owner_mime
                        if not options['dry_run']:
                            hash_owner.save(update_fields=['file_size', 'mime_type', 'updated_at'])
                        if owner_size == file_size:
                            duplicate = hash_owner
                if duplicate is None and hash_owner is not None:
                    raise ValueError('SHA-256 collision with mismatched file size')
                if duplicate is not None and duplicate.id != asset.id:
                    if duplicate.file_size != file_size:
                        raise ValueError('SHA-256 collision with mismatched file size')
                    updates = []
                    if not duplicate.mime_type and mime_type:
                        duplicate.mime_type = mime_type
                        updates.append('mime_type')
                    if duplicate.file_size is None:
                        duplicate.file_size = file_size
                        updates.append('file_size')
                    if updates and not options['dry_run']:
                        duplicate.save(update_fields=[*updates, 'updated_at'])
                    self.stdout.write(f'{asset.id} -> {duplicate.id} {content_hash}')
                    if not options['dry_run']:
                        self._merge_asset(asset, duplicate)
                    merged += 1
                    continue
                self.stdout.write(f'{asset.id} {asset.source_key} {content_hash}')
                if not options['dry_run']:
                    asset.content_hash = content_hash
                    asset.file_size = file_size
                    if mime_type:
                        asset.mime_type = mime_type
                    asset.save(update_fields=['content_hash', 'file_size', 'mime_type', 'updated_at'])
                completed += 1
            except Exception as error:
                failed += 1
                self.stderr.write(f'{asset_id}: {error}')
        self.stdout.write(self.style.SUCCESS(
            f'completed={completed} merged={merged} failed={failed}',
        ))
