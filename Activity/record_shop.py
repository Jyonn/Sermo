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
MEDAL_TIERS = ('diamond', 'obsidian')
PROVIDERS = {'netease_music', 'qq_music', 'pocklo_music'}
SINGERS = {
    'jay': ('周杰伦', 'Jay Chou'),
    'leehom': ('王力宏', 'Leehom Wang', 'Wang Leehom'),
    'david': ('陶喆', '陶吉吉', 'David Tao', 'Tao Zhe'),
    'jj': ('林俊杰', 'JJ Lin', 'Lin Junjie'),
}
WOMEN_SINGERS = {
    'jolin': ('蔡依林', 'Jolin', 'Jolin Tsai', 'JOLIN蔡依林'),
    'stefanie': ('孙燕姿', '孫燕姿', 'Stefanie Sun', 'Yanzi Sun', 'Sun Yanzi'),
    'elva': ('萧亚轩', '蕭亞軒', 'Elva Hsiao', 'Elva'),
    'fish': ('梁静茹', '梁靜茹', 'Fish Leong', 'Fish'),
}
ROSTERS = {'classic': SINGERS, 'women': WOMEN_SINGERS}
PLAYBACK_SALT = 'record-shop.playback.v1'
PLAYBACK_MAX_AGE = timedelta(hours=6)
SIMPLIFY = OpenCC('t2s')


@Error.register
class RecordShopErrors:
    INVALID = Error(_('Invalid record shop action'), code=Code.BadRequest)
    UNAVAILABLE = Error(_('The record shop campaign is unavailable'), code=Code.Forbidden)
    NO_SELECTION = Error(_('There is no singer selection available'), code=Code.Forbidden)
    NOT_CLAIMED = Error(_('Claim this singer bubble before using it'), code=Code.Forbidden)


def normalized(value):
    if not isinstance(value, str):
        return ''
    value = SIMPLIFY.convert(unicodedata.normalize('NFKC', value)).casefold()
    return ''.join(char for char in value if char.isalnum())


SINGER_IDS = {
    edition: {normalized(alias): singer for singer, aliases in roster.items() for alias in aliases}
    for edition, roster in ROSTERS.items()
}


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
    roster_ids = SINGER_IDS[config.get('roster', 'classic')]
    featured = {roster_ids[name] for name in artist_names if name in roster_ids}
    if featured:
        # Keep the track keys used by existing activity scores and playback receipts.
        singer = min(featured)
        aliases = config.get('title_aliases', {}).get(singer, {})
        return f'{singer}:{aliases.get(title) or title}'
    return f'other:{"+".join(sorted(artist_names))}:{title}'


def record_shop_runs(user, *, active_only=True):
    runs = ActivityService.space_activities(user.space, active_only=active_only).filter(campaign__config__mode=MODE)
    if user.is_official or user.is_deleted:
        return runs.none()
    return runs


def active_run(user, key=None):
    runs = record_shop_runs(user)
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
    runs = [run for run in record_shop_runs(source.user) if run.is_active(source.created_at)]
    for run in runs:
        RecordShopShare.objects.get_or_create(
            run=run, surface=surface, source_id=source.pk,
            defaults={'user': source.user, 'preview': preview, 'published_at': source.created_at},
        )
    if runs:
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
    featured = song.partition(':')[0] in ROSTERS[run.campaign.config.get('roster', 'classic')]
    if abs(duration - expected) <= 1200 and (expected <= PREVIEW_LIMIT_MS or duration > PREVIEW_LIMIT_MS):
        points = FULL_TRACK_POINTS[featured]
    elif provider != 'pocklo_music' and expected > PREVIEW_LIMIT_MS and duration <= PREVIEW_LIMIT_MS:
        points = PREVIEW_POINTS[featured]
    else:
        return {'points': 0}
    return {'points': award(run, user, 'listen', song, points)}


def progress_payload(progress, active):
    from Activity.reruns import reward_growth
    preference = RecordShopPreference.objects.filter(progress=progress).first()
    singers = preference.singers if preference else []
    return dict(
        points=progress.earned_points, thresholds=THRESHOLDS,
        singers=singers, equipped=preference.equipped if preference else '',
        portraits=progress.campaign.config.get('portraits', {}),
        albums={singer: [public_album(album) for album in albums] for singer, albums in
                progress.campaign.config.get('albums', {}).items()},
        choices=max(0, sum(progress.earned_points >= threshold for threshold in THRESHOLDS) - len(singers)) if active else 0,
        rerun_growth=reward_growth(progress.campaign, progress.user),
    )


def public_album(album):
    return {key: album[key] for key in ('id', 'name', 'title', 'tier', 'cover', 'clip') if album.get(key)}


def album_medal_snapshot(artist, album):
    return {
        'artist': artist,
        'album': {field: album[field] for field in ('id', 'name', 'title', 'tier', 'cover')},
    }


def medal_catalog(config):
    return {
        album['id']: album_medal_snapshot(artist, album)
        for artist, catalog in config.get('albums', {}).items()
        for album in catalog if album['tier'] in MEDAL_TIERS
    }


def album_medal_payload(medal, snapshot):
    return {
        **snapshot,
        'user': medal.user.dictify('user_id', 'name', 'avatar_uri', 'avatar_cache_key', 'avatar_frame_style'),
        'discovered_at': medal.discovered_at.timestamp(),
    }


def space_first_album_medals(space_id):
    runs = SpaceActivity.objects.select_related('campaign').filter(space_id=space_id, campaign__config__mode=MODE)
    catalogs = {run.pk: medal_catalog(run.campaign.config) for run in runs}
    medals = RecordShopAnnouncement.objects.select_related('user').filter(
        run_id__in=catalogs, user__space_id=space_id, user__is_deleted=False,
    ).order_by('-discovered_at', '-id')
    rows = []
    for medal in medals:
        item = medal.album_snapshot or catalogs[medal.run_id].get(medal.album_id)
        if item is None:
            continue
        rows.append(album_medal_payload(medal, item))
    return rows


def current_album_clip_path(album_id):
    config = ActivityCampaign.objects.filter(key='record-shop', config__mode=MODE).values_list('config', flat=True).first()
    if not config:
        return None
    clip = next(
        (album.get('clip') for albums in config.get('albums', {}).values() for album in albums if album['id'] == album_id),
        config.get('retired_clips', {}).get(album_id),
    )
    if isinstance(clip, str):
        path = urlsplit(clip).path
        if re.fullmatch(r'/resources/assets/[1-9][0-9]*/display/[a-f0-9]{64}', path):
            return path
    return None


@transaction.atomic
def claim_singer(key, user, singer):
    from Activity.reruns import record_reward
    from User.models import User

    run = record_shop_runs(user, active_only=False).filter(campaign__key=key, claimed_at__lte=timezone.now()).first()
    if run is None:
        raise RecordShopErrors.UNAVAILABLE
    if not isinstance(singer, str) or singer not in ROSTERS[run.campaign.config.get('roster', 'classic')]:
        raise RecordShopErrors.INVALID
    User.objects.select_for_update().get(pk=user.pk)
    progress = _locked_progress(run, user)
    preference, _ = RecordShopPreference.objects.get_or_create(progress=progress)
    if singer not in preference.singers:
        if not run.is_active():
            raise RecordShopErrors.UNAVAILABLE
        if len(preference.singers) >= sum(progress.earned_points >= threshold for threshold in THRESHOLDS):
            raise RecordShopErrors.NO_SELECTION
        if run.campaign.edition > 1:
            owned = any(singer in singers for singers in _bubble_preferences(user).exclude(
                pk=preference.pk,
            ).values_list('singers', flat=True))
            record_reward(run.campaign, user, f'record-shop:{singer}', owned)
        preference.singers.append(singer)
        preference.save(update_fields=['singers'])
    return progress_payload(progress, run.is_active())


def _bubble_preferences(user):
    return RecordShopPreference.objects.filter(
        progress__user=user, progress__space_activity__space_id=user.space_id,
        progress__campaign__config__mode=MODE,
    )


def bubble_preferences_payload(user):
    rows = _bubble_preferences(user).filter(progress__campaign__enabled=True).order_by('-pk')
    claimed, equipped = set(), ''
    for singers, active in rows.values_list('singers', 'equipped'):
        claimed.update(singers)
        equipped = equipped or active
    return {'claimed': sorted(claimed), 'equipped': equipped}


def set_equipped_singer(user, singer):
    """Change the worn bubble under the same user lock as claim_singer."""
    preferences = _bubble_preferences(user)
    if singer == '':
        preferences.exclude(equipped='').update(equipped='')
        return
    if not isinstance(singer, str) or not any(singer in roster for roster in ROSTERS.values()):
        raise RecordShopErrors.INVALID
    owned = next((row for row in preferences.filter(progress__campaign__enabled=True).order_by('-pk')
                  if singer in row.singers), None)
    if owned is None:
        raise RecordShopErrors.NOT_CLAIMED
    preferences.exclude(pk=owned.pk).exclude(equipped='').update(equipped='')
    if owned.equipped != singer:
        owned.equipped = singer
        owned.save(update_fields=['equipped'])


def decorate_message(message):
    from Message.models import Message, MessageTypeChoice

    if message.chat.submission or message.user.is_official:
        return
    preference = _bubble_preferences(message.user).select_related('progress__space_activity__campaign').filter(
        progress__campaign__enabled=True,
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
            if album['tier'] in MEDAL_TIERS:
                RecordShopAnnouncement.objects.get_or_create(
                    run=run, album_id=album['id'],
                    defaults={'user': message.user, 'discovered_at': message.created_at,
                              'album_snapshot': album_medal_snapshot(artist, album)},
                )
    message.appearance = {**message.appearance, 'record_shop': decoration}
    message.save(update_fields=['appearance'])
