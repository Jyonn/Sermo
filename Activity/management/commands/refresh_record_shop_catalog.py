import json

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Activity.management.commands.configure_record_shop import EDITIONS, validate_manifest
from Activity.models import ActivityCampaign, RecordShopAnnouncement
from Activity.record_shop import MODE, ROSTERS, medal_catalog
from Activity.record_shop_studio_catalog import studio_album_pool


class Command(BaseCommand):
    help = 'Preview or apply the studio album catalog for both editions of Echo Record Shop.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Update both existing campaigns.')

    @transaction.atomic
    def handle(self, *args, **options):
        keys = [key for key, _, _ in EDITIONS.values()]
        campaigns = ActivityCampaign.objects.select_for_update().filter(key__in=keys, config__mode=MODE).in_bulk(field_name='key')
        for edition, (key, _, _) in EDITIONS.items():
            campaign = campaigns.get(key)
            if not campaign:
                raise CommandError(f'Missing existing campaign: {key}')

            previous = campaign.config
            albums = previous.get('albums', {})
            try:
                updated = {artist: studio_album_pool(artist, albums[artist]) for artist in ROSTERS[edition]}
            except KeyError as error:
                raise CommandError(f'{key}: missing artist or studio album {error}') from error

            active_ids = {row['id'] for pool in updated.values() for row in pool}
            retired = {row['id']: row['clip'] for pool in albums.values() for row in pool
                       if row['id'] not in active_ids and row.get('clip')}
            config = {
                **previous,
                'albums': updated,
                'retired_clips': {**previous.get('retired_clips', {}), **retired},
            }
            validate_manifest(config, edition)

            if options['apply']:
                medals = RecordShopAnnouncement.objects.filter(run__campaign=campaign, album_snapshot__isnull=True)
                for album_id, snapshot in medal_catalog(previous).items():
                    medals.filter(album_id=album_id).update(album_snapshot=snapshot)
                campaign.config = config
                campaign.save(update_fields=['config'])

            summary = {artist: {
                'previous': len(albums[artist]),
                'studio': len(pool),
                'obsidian': pool[-1]['name'],
            } for artist, pool in updated.items()}
            self.stdout.write(f'{key} {json.dumps(summary, ensure_ascii=False)}')
        self.stdout.write(self.style.SUCCESS('Updated both catalogs.' if options['apply'] else 'Validated both proposed catalogs.'))
