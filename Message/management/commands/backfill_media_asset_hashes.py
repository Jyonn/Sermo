import hashlib

import requests
from django.core.management.base import BaseCommand

from Message.models import MediaAsset
from utils.qiniu import sign_private_download_url


class Command(BaseCommand):
    help = 'Backfill SHA-256 hashes for cloud video and file resources.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=0)
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        queryset = MediaAsset.objects.filter(kind__in=(MediaAsset.KIND_VIDEO, MediaAsset.KIND_FILE)).order_by('id')
        if not options['force']:
            queryset = queryset.filter(content_hash='')
        if options['limit'] > 0:
            queryset = queryset[:options['limit']]

        completed = failed = 0
        for asset in queryset.iterator(chunk_size=20):
            try:
                digest = hashlib.sha256()
                with requests.get(sign_private_download_url(asset.source_uri), stream=True, timeout=(10, 120)) as response:
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            digest.update(chunk)
                content_hash = digest.hexdigest()
                self.stdout.write(f'{asset.id} {asset.source_key} {content_hash}')
                if not options['dry_run']:
                    asset.content_hash = content_hash
                    asset.save(update_fields=['content_hash', 'updated_at'])
                completed += 1
            except Exception as error:
                failed += 1
                self.stderr.write(f'{asset.id} {asset.source_key}: {error}')
        self.stdout.write(self.style.SUCCESS(f'completed={completed} failed={failed}'))
