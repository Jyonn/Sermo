from django.core.management.base import BaseCommand

from Message.models import MediaAsset
from Message.video_transcode import submit_video_transcode


class Command(BaseCommand):
    help = 'Submit existing videos for Qiniu MP4 transcoding in bounded batches.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--retry-failed', action='store_true')

    def handle(self, *args, **options):
        statuses = [MediaAsset.TRANSCODE_NONE]
        if options['retry_failed']:
            statuses.append(MediaAsset.TRANSCODE_FAILED)
        ids = list(MediaAsset.objects.filter(kind=MediaAsset.KIND_VIDEO, transcode_status__in=statuses).order_by('id').values_list('id', flat=True)[:max(1, options['limit'])])
        if options['retry_failed']:
            MediaAsset.objects.filter(id__in=ids,transcode_status=MediaAsset.TRANSCODE_FAILED).update(transcode_attempts=0)
        for asset_id in ids:
            submit_video_transcode(asset_id)
        self.stdout.write(self.style.SUCCESS(f'submitted={len(ids)}'))
