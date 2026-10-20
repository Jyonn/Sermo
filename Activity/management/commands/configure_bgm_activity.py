import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Activity.bgm import BgmService
from Activity.models import ActivityCampaign
from Message.models import AudioTranscript, AudioTranscriptStatusChoice, MediaAsset
from django.utils import timezone


class Command(BaseCommand):
    help = 'Register the 12 supplied BGM clips as MediaAssets; enable only after publishing the audio files.'

    def add_arguments(self, parser):
        parser.add_argument('--directory', required=True, help='Directory containing catalog.json and 01.mp3 through 12.mp3')
        parser.add_argument('--origin', required=True, help='Public HTTPS origin hosting /activity/bgm/')
        parser.add_argument('--enable', action='store_true', help='Expose for space owners to claim; does not start any space activity')

    def handle(self, *args, **options):
        origin = options['origin'].rstrip('/')
        parsed = urlparse(origin)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise CommandError('origin must be a bare HTTPS origin')
        directory = Path(options['directory'])
        try:
            rows = json.loads((directory / 'catalog.json').read_text())
            if not isinstance(rows, list) or len(rows) != 12:
                raise ValueError('exactly 12 tracks required')
            for index, row in enumerate(rows):
                if row['file'] != f'{index + 1:02d}.mp3' or not 0 < float(row['duration']) <= 20:
                    raise ValueError('invalid file name or duration')
                if hashlib.sha256((directory / row['file']).read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('audio checksum mismatch')
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError(str(error))
        with transaction.atomic():
            campaign, created = ActivityCampaign.objects.get_or_create(key='main-character-bgm-2026', defaults=dict(
                title='主角登场', title_en='Make an Entrance',
                summary='在广场收集灵感，解锁你的主角声场。',
                summary_en='Post, connect and collect your soundtrack.',
                duration_seconds=30 * 86400, event_key='bgm.interaction', enabled=False,
                config={'mode': BgmService.MODE, 'theme': 'bgm'},
            ))
            if not created and campaign.space_runs.exists():
                raise CommandError('Already claimed: do not replace a running or completed catalog')
            tracks = []
            for row in rows:
                key = f'activity/bgm/{row["file"]}'
                asset, _ = MediaAsset.objects.update_or_create(source_key=key, defaults=dict(
                    source_uri=f'{origin}/{key}', kind=MediaAsset.KIND_AUDIO,
                    mime_type='audio/mpeg', status=MediaAsset.STATUS_READY,
                    duration_seconds=row['duration'], file_size=(directory / row['file']).stat().st_size,
                    raw_metadata={'bgm_public': True}, geocoding_status=MediaAsset.GEOCODING_UNAVAILABLE,
                ))
                tracks.append(dict(asset_id=asset.id, title=row['title'], author=row.get('author', '')))
                AudioTranscript.objects.update_or_create(asset=asset, defaults=dict(
                    status=AudioTranscriptStatusChoice.READY,
                    text=' · '.join(part for part in (row['title'], row.get('author', '')) if part),
                    completed_at=timezone.now(),
                ))
            campaign.config = {'mode': BgmService.MODE, 'theme': 'bgm', 'tracks': tracks}
            if not BgmService.catalog(campaign):
                raise CommandError('Catalog failed validation')
            campaign.enabled = options['enable']
            campaign.save(update_fields=['config', 'enabled'])
        self.stdout.write(self.style.SUCCESS(f'{campaign.key}: 12 tracks ready; enabled={campaign.enabled}. Claim through the space activity panel to start 30 days.'))
