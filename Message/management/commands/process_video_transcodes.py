from django.core.management.base import BaseCommand
from django.utils import timezone

from Message.models import MediaAsset
from Message.video_transcode import ORIGINAL_RETENTION, delete_expired_original, refresh_video_transcode


class Command(BaseCommand):
    help = 'Poll pending Qiniu video transcodes and delete source videos after seven days.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, *args, **options):
        limit = max(1, options['limit'])
        pending_ids = list(MediaAsset.objects.filter(transcode_status=MediaAsset.TRANSCODE_PENDING).values_list('id', flat=True)[:limit])
        for asset_id in pending_ids:
            refresh_video_transcode(asset_id)
        cleanup_ids = list(MediaAsset.objects.filter(
            transcode_status=MediaAsset.TRANSCODE_READY,
            original_deleted_at__isnull=True,
            transcoded_at__lte=timezone.now() - ORIGINAL_RETENTION,
        ).values_list('id', flat=True)[:limit])
        deleted = sum(bool(delete_expired_original(asset_id)) for asset_id in cleanup_ids)
        self.stdout.write(self.style.SUCCESS(f'polled={len(pending_ids)} originals_deleted={deleted}'))
