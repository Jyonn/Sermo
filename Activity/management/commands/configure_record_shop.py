import json
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from Activity.models import ActivityCampaign
from Activity.record_shop import MODE, SINGERS, normalized


TIERS = ('gold', 'platinum', 'diamond', 'obsidian')
AUDIO_SUFFIXES = {'.m4a', '.mp3', '.aac', '.opus', '.ogg', '.wav', '.flac'}
MANAGED_ASSET = re.compile(r'/resources/assets/[1-9][0-9]*/display/[a-f0-9]{64}')


def https_asset(value):
    if not isinstance(value, str) or not value or len(value) > 1000 or any(char.isspace() for char in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme == 'https' and bool(url.hostname and url.path) and not (url.username or url.password)
    except ValueError:
        return False


def audio_asset(value):
    if not https_asset(value):
        return False
    path = urlsplit(value).path
    return Path(path).suffix.lower() in AUDIO_SUFFIXES or bool(MANAGED_ASSET.fullmatch(path))


def validate_manifest(value):
    if not isinstance(value, dict):
        raise CommandError('Expected a JSON object')
    pools, portraits, titles = (value.get(key) for key in ('albums', 'portraits', 'verified_titles'))
    for label, mapping in [('albums', pools), ('portraits', portraits), ('verified_titles', titles)]:
        if not isinstance(mapping, dict) or set(mapping) != set(SINGERS):
            raise CommandError(f'{label}: expected all four artist IDs')

    album_ids = set()
    missing_clips = []
    for artist in SINGERS:
        if not https_asset(portraits[artist]):
            raise CommandError(f'{artist}: missing WebP portrait URL')
        if not isinstance(titles[artist], list) or not titles[artist] or any(not normalized(title) for title in titles[artist]):
            raise CommandError(f'{artist}: no verified track titles')
        pool = pools[artist]
        if not isinstance(pool, list) or len(pool) < 4 or any(not isinstance(album, dict) for album in pool):
            raise CommandError(f'{artist}: expected an album pool with four rarity tiers')
        masses = {tier: [] for tier in TIERS}
        for album in pool:
            key, tier, probability = (album.get(field) for field in ('id', 'tier', 'probability'))
            if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9-]{1,80}', key) or key in album_ids:
                raise CommandError(f'{artist}: invalid or duplicate album ID {key!r}')
            album_ids.add(key)
            if not normalized(album.get('name')) or not normalized(album.get('title')) or not https_asset(album.get('cover')):
                raise CommandError(f'{key}: missing release name, cover, or track name')
            if not isinstance(tier, str) or tier not in masses or type(probability) not in (int, float) or not math.isfinite(probability) or not 0 < probability < 1:
                raise CommandError(f'{key}: invalid probability or rarity')
            masses[tier].append(probability)
            clip = album.get('clip')
            if not audio_asset(clip):
                missing_clips.append(key)
        if any(not values for values in masses.values()) or len(masses['obsidian']) != 1 or masses['obsidian'][0] >= .001:
            raise CommandError(f'{artist}: expected one Obsidian cover below 0.1% and all other rarity tiers')
        if not math.isclose(sum(map(sum, masses.values())), 1, abs_tol=1e-8):
            raise CommandError(f'{artist}: probabilities must add up to 100%')
        pool_mass = 1 - masses['obsidian'][0]
        for tier, fraction in zip(TIERS, (.6, .3, .1)):
            if not math.isclose(sum(masses[tier]), pool_mass * fraction, abs_tol=1e-8):
                raise CommandError(f'{artist}: {tier} has the wrong probability mass')
        if any(min(masses[a]) <= max(masses[b]) for a, b in zip(TIERS, TIERS[1:])):
            raise CommandError(f'{artist}: an album is more likely than an album in the previous tier')
    if missing_clips:
        raise CommandError(f'Missing clipped recordings for {len(missing_clips)} albums: {", ".join(missing_clips)}')
    return {
        'mode': MODE, 'theme': 'record-shop', 'albums': pools, 'portraits': portraits,
        'verified_titles': {artist: sorted({normalized(title) for title in rows}) for artist, rows in titles.items()},
        'title_aliases': value.get('title_aliases', {}),
        'presentation': {'palette': 'gold', 'title_art_url': '/assets/activity/wordmarks/record-shop.webp'},
    }


class Command(BaseCommand):
    help = 'Check a complete record shop catalog or make it available for space administrators to claim.'

    def add_arguments(self, parser):
        parser.add_argument('manifest', type=Path)
        actions = parser.add_mutually_exclusive_group()
        actions.add_argument('--enable', action='store_true')
        actions.add_argument('--replace-clips', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        try:
            config = validate_manifest(json.loads(options['manifest'].read_text(encoding='utf-8')))
        except (ValueError, OSError) as error:
            raise CommandError(str(error)) from error
        count = sum(map(len, config['albums'].values()))
        if options['replace_clips']:
            campaign = ActivityCampaign.objects.select_for_update().filter(key='record-shop', config__mode=MODE).first()
            if not campaign:
                raise CommandError('No existing Record Shop campaign')
            def without_clips(pools):
                return {artist: [{k: v for k, v in album.items() if k != 'clip'} for album in albums]
                        for artist, albums in pools.items()}
            if without_clips(config['albums']) != without_clips(campaign.config['albums']):
                raise CommandError('Clip replacement must preserve every album, cover, and probability')
            campaign.config['albums'] = config['albums']
            campaign.save(update_fields=['config'])
            self.stdout.write(self.style.SUCCESS(f'Updated {count} Record Shop clips.'))
            return
        if not options['enable']:
            self.stdout.write(self.style.SUCCESS(f'Validated {count} covers and clip references.'))
            return
        campaign = ActivityCampaign.objects.select_for_update().filter(key='record-shop').first()
        if campaign and campaign.space_runs.exists():
            raise CommandError('A space has already claimed this release. Keep album IDs and odds fixed for its run.')
        ActivityCampaign.objects.update_or_create(key='record-shop', defaults={
            'title': '回声唱片铺', 'title_en': 'Echo Record Shop',
            'summary': '分享歌曲，收藏歌手气泡。', 'summary_en': 'Share songs and collect artist bubbles.',
            'assignment_mode': ActivityCampaign.AssignmentMode.MANUAL, 'duration_seconds': 30 * 86400,
            'event_key': 'record-shop.share', 'config': config, 'enabled': True,
        })
        self.stdout.write(self.style.SUCCESS(f'Record Shop is available for space administrators with {count} covers.'))
