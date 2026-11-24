"""Record Shop campaign scoring for resolved music links and playback receipts."""
import re
import random
import unicodedata
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from django.core import signing
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from opencc import OpenCC
from smartdjango import Code, Error

from Activity.models import ActivityCampaign, ActivityEvent, ActivityService, RecordShopAnnouncement, RecordShopDiscovery, RecordShopPreference, RecordShopShare, SpaceActivity, UserActivityProgress


MODE = 'record_shop'
SHARE_POINTS = {'chat': 5, 'square': 10}
FULL_TRACK_POINTS = {True: 6, False: 4}
PREVIEW_POINTS = {True: 3, False: 2}
PREVIEW_LIMIT_MS = 30_000
THRESHOLDS = (30, 60)
PROVIDERS = {'netease_music', 'qq_music', 'pocklo_music'}
SINGERS = {
    'jay': ('周杰伦', 'Jay Chou'),
    'leehom': ('王力宏', 'Leehom Wang', 'Wang Leehom'),
    'david': ('陶喆', '陶吉吉', 'David Tao', 'Tao Zhe'),
    'jj': ('林俊杰', 'JJ Lin', 'Lin Junjie'),
}
PLAYBACK_SALT = 'record-shop.playback.v1'
PLAYBACK_MAX_AGE = timedelta(hours=6)
SIMPLIFY = OpenCC('t2s')


@Error.register
class RecordShopErrors:
    INVALID = Error(_('Invalid record shop action'), code=Code.BadRequest)
    UNAVAILABLE = Error(_('The record shop campaign is unavailable'), code=Code.Forbidden)
    NO_SELECTION = Error(_('There is no singer selection available'), code=Code.Forbidden)


def normalized(value):
    if not isinstance(value, str):
        return ''
    value = SIMPLIFY.convert(unicodedata.normalize('NFKC', value)).casefold()
    return ''.join(char for char in value if char.isalnum())


SINGER_IDS = {normalized(alias): singer for singer, aliases in SINGERS.items() for alias in aliases}


def track_identity(music, config):
    if not isinstance(music, dict) or music.get('provider') not in PROVIDERS or not music.get('song_id'):
        return None
    if music['provider'] == 'pocklo_music' and not music.get('available'):
        return None
    title = re.sub(r'\s*[（(]\s*(?:feat\.?|ft\.?|with)\s+[^)）]+[)）]\s*$', '', str(music.get('title') or ''), flags=re.IGNORECASE)
    title = normalized(title)
    artists = music.get('artists')
    if not title or not isinstance(artists, list):
        return None
    artist_names = {normalized(artist) for artist in artists} - {''}
    if not artist_names:
        return None
    featured = {SINGER_IDS[name] for name in artist_names if name in SINGER_IDS}
    if featured:
        # Keep the track keys used by existing activity scores and playback receipts.
        singer = min(featured)
        aliases = config.get('title_aliases', {}).get(singer, {})
        return f'{singer}:{aliases.get(title) or title}'
    return f'other:{"+".join(sorted(artist_names))}:{title}'


def active_run(user, key=None):
    if user.is_official or user.is_deleted:
        return None
    runs = ActivityService.space_activities(user.space, active_only=True).filter(campaign__config__mode=MODE)
    if key:
        runs = runs.filter(campaign__key=key)
    return runs.first()


def _locked_progress(run, user):
    _, progress = ActivityService._progress(run.campaign, user, run)
    return UserActivityProgress.objects.select_for_update().get(pk=progress.pk)


@transaction.atomic
def award(run, user, kind, song, points, occurred_at=None):
    occurred_at = occurred_at or timezone.now()
    if not run.is_active(occurred_at) or not run.campaign.enabled or run.space_id != user.space_id:
        return 0
    progress = _locked_progress(run, user)
    _, created = ActivityEvent.objects.get_or_create(
        campaign=run.campaign, progress=progress, event_key=f'record-shop.{kind}',
        event_date=timezone.localdate(run.claimed_at, ActivityService.BEIJING_TIMEZONE),
        event_reference=sha256(song.encode()).hexdigest(),
        defaults={'points': points, 'claimed_at': occurred_at},
    )
    if created:
        UserActivityProgress.objects.filter(pk=progress.pk).update(earned_points=F('earned_points') + points, updated_at=timezone.now())
    return points if created else 0


def register_share(source, surface, preview):
    from Message.models import LinkPreview
    from Message.providers import pocklo
    from Message.providers.music.provider import MusicProvider

    if preview is None:
        return
    hostname = urlsplit(preview.url).hostname
    if hostname not in LinkPreview.NETEASE_HOSTS | MusicProvider.QQ_HOSTS and not pocklo.share_token(preview.url):
        return
    run = active_run(source.user)
    if run is None or not run.is_active(source.created_at):
        return
    RecordShopShare.objects.get_or_create(
        surface=surface, source_id=source.pk,
        defaults={'run': run, 'user': source.user, 'preview': preview, 'published_at': source.created_at},
    )
    transaction.on_commit(lambda: resolve_shares(preview.pk), robust=True)


def resolve_shares(preview_id):
    from Message.models import LinkPreview, LinkPreviewStatusChoice

    preview = LinkPreview.objects.filter(pk=preview_id, status=LinkPreviewStatusChoice.READY).first()
    if preview is None:
        return
    pending = RecordShopShare.objects.filter(preview=preview, resolved=False)
    for share_id in pending.order_by('published_at', 'id').values_list('id', flat=True).iterator():
        with transaction.atomic():
            share = pending.select_for_update().filter(pk=share_id).first()
            if share is None:
                continue
            song = track_identity(preview.provider_data or {}, share.run.campaign.config)
            if song and not share.user.is_deleted:
                award(share.run, share.user, 'share', song, SHARE_POINTS[share.surface], share.published_at)
            share.resolved = True
            share.save(update_fields=['resolved'])


def get_playback_source(user, surface, source_id):
    from Message.models import LinkPreview, LinkPreviewStatusChoice, Message, MessageTypeChoice
    from Square.models import Statement

    if type(source_id) is not int or not 0 < source_id < 2 ** 63:
        return None
    if surface == 'square' and user.space.group_square_enabled and user.space.verification_tier != 'email':
        statement = Statement.visible_for(user).select_related('link_preview').filter(pk=source_id).first()
        preview = statement.link_preview if statement else None
    elif surface == 'chat' and user.space.chat_enabled:
        message = Message.objects.select_related('chat').filter(pk=source_id, type=MessageTypeChoice.TEXT).first()
        if not message or not message.chat.has_active_member(user) or not message.is_visible_to(user):
            return None
        preview = LinkPreview.queue_for_text(message.content)
    else:
        return None
    return preview.provider_data if preview and preview.status == LinkPreviewStatusChoice.READY else None


def start_listen(key, user, data):
    run = active_run(user, key)
    if run is None:
        return {'receipt': None}
    surface, source_id = data.get('surface'), data.get('source_id')
    music = get_playback_source(user, surface, source_id)
    song = track_identity(music, run.campaign.config) if music else None
    if not song or music.get('provider') != data.get('provider') or str(music['song_id']) != str(data.get('song_id')):
        return {'receipt': None}
    token = signing.dumps([user.pk, run.pk, surface, source_id, song, music['provider'], str(music['song_id']), timezone.now().timestamp()],
                          salt=PLAYBACK_SALT)
    return {'receipt': token}


def played_complete(duration, ranges):
    if type(duration) is not int or not 0 < duration <= 2 * 3600 * 1000:
        return False
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= 64:
        return False
    for item in ranges:
        if not isinstance(item, list) or len(item) != 2 or any(type(offset) is not int for offset in item):
            return False
        if not 0 <= item[0] < item[1] <= duration + 50:
            return False
    missed, covered = 0, 0
    for start, end in sorted(ranges):
        missed += max(0, start - covered)
        covered = max(covered, end)
    return missed + max(0, duration - covered) <= 50


def finish_listen(key, user, data):
    run = active_run(user, key)
    if run is None:
        return {'points': 0}
    duration = data.get('duration_ms')
    token = data.get('receipt')
    if data.get('ended') is not True or not played_complete(duration, data.get('played')) or not isinstance(token, str) or len(token) > 2048:
        return {'points': 0}
    try:
        owner, run_id, surface, source_id, song, provider, provider_id, started_at = signing.loads(
            token, salt=PLAYBACK_SALT, max_age=PLAYBACK_MAX_AGE,
        )
    except (signing.BadSignature, ValueError, TypeError):
        return {'points': 0}
    if owner != user.pk or run_id != run.pk or (timezone.now().timestamp() - started_at) * 1000 + 300 < duration:
        return {'points': 0}
    music = get_playback_source(user, surface, source_id)
    if (not music or provider != music.get('provider') or provider_id != str(music.get('song_id'))
            or song != track_identity(music, run.campaign.config)):
        return {'points': 0}
    expected = music.get('duration_ms')
    if type(expected) is not int or expected <= 0:
        return {'points': 0}
    featured = song.partition(':')[0] in SINGERS
    if abs(duration - expected) <= 1200 and (expected <= PREVIEW_LIMIT_MS or duration > PREVIEW_LIMIT_MS):
        points = FULL_TRACK_POINTS[featured]
    elif provider != 'pocklo_music' and expected > PREVIEW_LIMIT_MS and duration <= PREVIEW_LIMIT_MS:
        points = PREVIEW_POINTS[featured]
    else:
        return {'points': 0}
    return {'points': award(run, user, 'listen', song, points)}


def progress_payload(progress, active):
    preference = RecordShopPreference.objects.filter(progress=progress).first()
    singers = preference.singers if preference else []
    return dict(
        points=progress.earned_points, thresholds=THRESHOLDS,
        singers=singers, equipped=preference.equipped if preference else '',
        portraits=progress.campaign.config.get('portraits', {}),
        albums={singer: [public_album(album) for album in albums] for singer, albums in
                progress.campaign.config.get('albums', {}).items()},
        choices=max(0, sum(progress.earned_points >= threshold for threshold in THRESHOLDS) - len(singers)) if active else 0,
    )


def public_album(album):
    return {key: album[key] for key in ('id', 'name', 'title', 'tier', 'cover', 'clip') if album.get(key)}


def space_first_album_medals(space_id):
    run = SpaceActivity.objects.select_related('campaign').filter(
        space_id=space_id, campaign__key='record-shop', campaign__config__mode=MODE,
    ).first()
    if run is None:
        return []

    albums = {
        album['id']: (artist, album)
        for artist, catalog in run.campaign.config.get('albums', {}).items()
        for album in catalog
        if album['tier'] in ('diamond', 'obsidian')
    }
    medals = RecordShopAnnouncement.objects.select_related('user').filter(
        run=run, album_id__in=albums, user__space_id=space_id, user__is_deleted=False,
    ).order_by('-discovered_at', '-id')
    rows = []
    for medal in medals:
        artist, album = albums[medal.album_id]
        rows.append({
            'album': {field: album[field] for field in ('id', 'name', 'title', 'tier', 'cover')},
            'artist': artist,
            'user': medal.user.dictify('user_id', 'name', 'avatar_uri', 'avatar_cache_key', 'avatar_frame_style'),
            'discovered_at': medal.discovered_at.timestamp(),
        })
    return rows


def current_album_clip_path(album_id):
    config = ActivityCampaign.objects.filter(key='record-shop', config__mode=MODE).values_list('config', flat=True).first()
    if not config:
        return None
    for albums in config.get('albums', {}).values():
        for album in albums:
            if album['id'] == album_id and isinstance(album.get('clip'), str):
                path = urlsplit(album['clip']).path
                if re.fullmatch(r'/resources/assets/[1-9][0-9]*/display/[a-f0-9]{64}', path):
                    return path
    return None


@transaction.atomic
def select_singer(key, user, singer):
    if not isinstance(singer, str) or singer not in SINGERS:
        raise RecordShopErrors.INVALID
    run = active_run(user, key)
    if run is None:
        raise RecordShopErrors.UNAVAILABLE
    progress = _locked_progress(run, user)
    preference, _ = RecordShopPreference.objects.get_or_create(progress=progress)
    if singer not in preference.singers:
        if len(preference.singers) >= sum(progress.earned_points >= threshold for threshold in THRESHOLDS):
            raise RecordShopErrors.NO_SELECTION
        preference.singers.append(singer)
    preference.equipped = singer
    preference.save(update_fields=['singers', 'equipped'])
    return progress_payload(progress, True)


def decorate_message(message):
    from Message.models import Message, MessageTypeChoice

    if message.chat.submission or message.user.is_official:
        return
    preference = RecordShopPreference.objects.select_related('progress__space_activity__campaign').filter(
        progress__user=message.user, progress__space_activity__space=message.user.space, progress__campaign__enabled=True,
    ).exclude(equipped='').order_by('-pk').first()
    if preference is None:
        return
    progress = preference.progress
    run = progress.space_activity
    artist = preference.equipped
    portrait = run.campaign.config.get('portraits', {}).get(artist)
    if not portrait:
        return
    albums = run.campaign.config.get('albums', {}).get(artist, [])
    active = run.is_active(message.created_at)
    if not active:
        owned = set(RecordShopDiscovery.objects.filter(progress=progress).values_list('album_id', flat=True))
        albums = [album for album in albums if album['id'] in owned]
    previous = Message.visible_in_chat(message.chat).filter(id__lt=message.id).order_by('-id').only(
        'id', 'type', 'user_id', 'created_at', 'appearance',
    ).first()
    groupable = (
        previous is not None and previous.user_id == message.user_id
        and previous.type not in {MessageTypeChoice.SYSTEM, MessageTypeChoice.OFFICIAL_NOTICE, MessageTypeChoice.SUBMISSION_INVITE}
        and previous.appearance.get('record_shop', {}).get('artist') == artist
        and previous.appearance.get('chat_bubble_style') == message.appearance.get('chat_bubble_style')
        and abs((message.created_at - previous.created_at).total_seconds()) < 300
        and timezone.localdate(message.created_at, ActivityService.BEIJING_TIMEZONE)
        == timezone.localdate(previous.created_at, ActivityService.BEIJING_TIMEZONE)
    )
    decoration = {'artist': artist, 'portrait': portrait}
    if albums and groupable:
        album, = random.choices(albums, weights=[row['probability'] for row in albums])
        decoration['album'] = public_album(album)
        if active:
            _, first_for_user = RecordShopDiscovery.objects.get_or_create(
                progress=progress, album_id=album['id'], defaults={'discovered_at': message.created_at},
            )
            decoration['first'] = first_for_user
            if first_for_user and album['tier'] in ('diamond', 'obsidian'):
                announcement, _ = RecordShopAnnouncement.objects.get_or_create(
                    run=run, album_id=album['id'], defaults={'user': message.user, 'discovered_at': message.created_at},
                )
                if not announcement.published:
                    transaction.on_commit(lambda: publish_announcement(announcement.pk), robust=True)
    message.appearance = {**message.appearance, 'record_shop': decoration}
    message.save(update_fields=['appearance'])


@transaction.atomic
def publish_announcement(announcement_id):
    from Space.models import Space
    from Square.models import Statement

    first = RecordShopAnnouncement.objects.select_for_update().filter(pk=announcement_id, published=False).first()
    if first is None:
        return
    space = Space.objects.select_for_update().get(pk=first.run.space_id)
    if space.verification_tier == 'email' or not space.group_square_enabled or not space.square_free_post_enabled:
        return
    album = next(
        (album for albums in first.run.campaign.config.get('albums', {}).values()
         for album in albums if album['id'] == first.album_id),
        None,
    )
    if album is None:
        return
    level = '曜' if album['tier'] == 'obsidian' else '钻'
    time = timezone.localtime(first.discovered_at, ActivityService.BEIJING_TIMEZONE).strftime('%m月%d日 %H:%M')
    text = f'恭喜{first.user.name[:30]}于{time}在回声唱片铺首次发现{level}级专辑《{album["name"][:55]}》！'
    first.statement = Statement.create_statement(space.ensure_official_user(), text, 'public', [])
    first.published = True
    first.save(update_fields=['statement', 'published'])
