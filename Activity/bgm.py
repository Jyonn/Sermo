"""Server-owned, space-scoped BGM collection rules. No client-awarded points."""
from collections import defaultdict
from Activity.presentation import presentation
from datetime import timedelta
import json
import random
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.models import ActivityCampaign, ActivityService, BgmEntrance, BgmInteraction, BgmParticipation
from Message.models import MediaAsset
from utils.qiniu import sign_private_download_url


@Error.register
class BgmErrors:
    INVALID = Error(_('Invalid activity settings'), code=Code.BadRequest)
    CLOSED = Error(_('This activity is not available'), code=Code.Forbidden)
    VERIFIED = Error(_('Please verify your account first'), code=Code.Forbidden)


class BgmService:
    MODE = 'bgm_collection'
    ZONE = ZoneInfo('Asia/Shanghai')
    # Day and point requirements are cumulative, never consecutive. First track is a gift.
    GATES = [(0, 0), (1, 10), (2, 25), (3, 40), (4, 60), (5, 80),
             (7, 105), (9, 130), (11, 155), (12, 180), (14, 210), (15, 240)]
    TASKS = {'publish': (1, 6), 'comment': (3, 3), 'like': (5, 1)}

    @classmethod
    def catalog(cls, campaign):
        tracks = campaign.config.get('tracks', [])
        if not isinstance(tracks, list) or len(tracks) != 12:
            return []
        ids = [item.get('asset_id') for item in tracks if isinstance(item, dict)]
        assets = {item.id: item for item in MediaAsset.objects.filter(
            id__in=[value for value in ids if isinstance(value, int)],
            kind=MediaAsset.KIND_AUDIO, status=MediaAsset.STATUS_READY,
        )}
        result = []
        for index, item in enumerate(tracks):
            if not isinstance(item, dict):
                return []
            asset = assets.get(item.get('asset_id'))
            if not asset or not asset.source_uri or not asset.duration_seconds or not 0 < asset.duration_seconds <= 20:
                return []
            if not str(item.get('title', '')).strip():
                return []
            days, points = cls.GATES[index]
            result.append(dict(key=str(index + 1), title=str(item['title'])[:120],
                               author=str(item.get('author') or '')[:120], days=days, points=points, asset=asset))
        if len(set(ids)) != 12:
            return []
        return result

    @classmethod
    def record(cls, user, kind, subject):
        if kind not in cls.TASKS or not user.verified or user.is_deleted:
            return
        # Only public non-anonymous content counts; never reward conversations with yourself.
        statement = subject.statement if kind == 'comment' else subject
        if statement.space_id != user.space_id or statement.is_deleted or statement.is_anonymous:
            return
        if statement.visibility != 0:
            return
        if kind != 'publish' and statement.user_id == user.id:
            return
        if kind == 'comment' and (subject.is_anonymous or subject.is_deleted or not subject.text.strip()):
            return
        if kind == 'publish' and not (statement.text.strip() or statement.media.exists()):
            return
        today = timezone.localdate(timezone=cls.ZONE)
        for run in ActivityService.space_activities(user.space, active_only=True).filter(campaign__config__mode=cls.MODE):
            if not cls.catalog(run.campaign):
                continue
            with transaction.atomic():
                _, progress = ActivityService._progress(run.campaign, user, run)
                progress = type(progress).objects.select_for_update().get(pk=progress.pk)
                if not BgmParticipation.objects.filter(progress=progress).exists():
                    continue
                reference = str(subject.id)
                target = str(statement.user_id if kind == 'like' else statement.id)
                rows = BgmInteraction.objects.filter(progress=progress, kind=kind)
                # Like/unlike cycles and repeat comments on the same statement never earn twice.
                if rows.filter(reference=reference).exists() or (kind != 'publish' and rows.filter(event_date=today, target=target).exists()):
                    continue
                limit, value = cls.TASKS[kind]
                points = value if rows.filter(event_date=today, points__gt=0).count() < limit else 0
                BgmInteraction.objects.create(progress=progress, kind=kind, reference=reference,
                                              target=target, event_date=today, points=points)

    @classmethod
    def stats(cls, progress):
        days = defaultdict(lambda: defaultdict(int))
        points = 0
        for row in progress.bgm_interactions.filter(points__gt=0):
            points += row.points
            days[row.event_date][row.kind] += 1
        active_days = sum(bool(counts['publish'] or counts['comment'] >= 2) for counts in days.values())
        today = days[timezone.localdate(timezone=cls.ZONE)]
        return points, active_days, today

    @classmethod
    def configure(cls, campaign, user, data):
        if not user.verified:
            raise BgmErrors.VERIFIED
        run = ActivityService.space_activity_for(campaign, user.space)
        if not cls.catalog(campaign):
            raise BgmErrors.CLOSED
        if not isinstance(data, dict) or set(data) - {'style', 'slots', 'mode', 'track', 'receive_audio'}:
            raise BgmErrors.INVALID
        style = data.get('style')
        if not isinstance(style, str) or style not in {'blue', 'pink'}:
            raise BgmErrors.INVALID
        with transaction.atomic():
            _, progress = ActivityService._progress(campaign, user, run)
            progress = type(progress).objects.select_for_update().get(pk=progress.pk)
            participation = BgmParticipation.objects.filter(progress=progress).first()
            if participation is None and (not campaign.enabled or not run.is_active()):
                raise BgmErrors.CLOSED
            points, days, _ = cls.stats(progress)
            owned = {str(index + 1) for index, (gate_days, gate_points) in enumerate(cls.GATES) if days >= gate_days and points >= gate_points}
            slots = data.get('slots', participation.slots if participation else ['1'])
            if not isinstance(slots, list) or len(slots) > 6 or any(not isinstance(key, str) or key not in owned for key in slots) or len(set(slots)) != len(slots):
                raise BgmErrors.INVALID
            mode = data.get('mode', participation.mode if participation else 'off')
            track = data.get('track', participation.track if participation else '1')
            receive = data.get('receive_audio', participation.receive_audio if participation else False)
            if not isinstance(mode, str) or mode not in {'off', 'auto', 'manual'} or not isinstance(track, str) or (track != 'random' and track not in owned) or not isinstance(receive, bool):
                raise BgmErrors.INVALID
            if mode == 'manual' and not slots:
                raise BgmErrors.INVALID
            BgmParticipation.objects.update_or_create(progress=progress, defaults={'style': style, 'slots': slots, 'mode': mode, 'track': track, 'receive_audio': receive})
        return cls.payload(campaign, user, run)

    @classmethod
    def payload(cls, campaign, user, run):
        _, progress = ActivityService._progress(campaign, user, run)
        participation = BgmParticipation.objects.filter(progress=progress).first()
        points, days, today = cls.stats(progress)
        catalog = cls.catalog(campaign)
        tracks = []
        for item in catalog:
            owned = bool(participation and days >= item['days'] and points >= item['points'])
            asset = item['asset']
            tracks.append({key: value for key, value in item.items() if key != 'asset'} | dict(
                owned=owned, duration=asset.duration_seconds,
                uri=(asset.source_uri if asset.raw_metadata.get('bgm_public') else sign_private_download_url(asset.playback_uri or asset.source_uri)) if owned else None,
            ))
        return dict(
            key=campaign.key, title=campaign.title, title_en=campaign.title_en,
            summary=campaign.summary, summary_en=campaign.summary_en,
            starts_at=run.claimed_at.timestamp(), ends_at=run.ends_at.timestamp() if run.ends_at else None,
            active=campaign.enabled and run.is_active() and bool(catalog), theme='bgm',
            presentation=presentation(campaign.config),
            assignment_mode=campaign.assignment_mode, duration_seconds=campaign.duration_seconds,
            newly_claimed=bool(run.attention_started_at and not run.user_views.filter(user=user).exists()),
            verified=bool(user.verified), today_earned=bool(today['publish'] or today['comment'] >= 2),
            claimable_points=0, available_points=points, contributed_points=0,
            personal_event_count=days, personal_event_target=15,
            personal_reward_claimable=False, personal_reward=None, space_reward_claimable=None,
            official_user=None, space_total=0, target=240, milestones=[], awakenings=[],
            bgm=dict(joined=participation is not None, style=participation.style if participation else None,
                     mode=participation.mode if participation else 'off', track=participation.track if participation else '1',
                     receive_audio=participation.receive_audio if participation else False,
                     slots=participation.slots if participation else [], points=points, active_days=days,
                     today_points=sum(today[kind] * value for kind, (_, value) in cls.TASKS.items()),
                     tasks=[dict(kind=kind, count=today[kind], limit=limit, points=value) for kind, (limit, value) in cls.TASKS.items()],
                     tracks=tracks, ready=bool(catalog)),
        )

    @classmethod
    def enter(cls, campaign, user, data):
        from Chat.models import Chat, ChatPurposeChoice
        from Message.models import MediaResource, Message, MessageEvent, MessageTypeChoice
        if not isinstance(data, dict) or not isinstance(data.get('chat_id'), int) or isinstance(data['chat_id'], bool):
            raise BgmErrors.INVALID
        request_id = data.get('request_id')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 80:
            raise BgmErrors.INVALID
        if not user.verified or user.is_deleted:
            raise BgmErrors.VERIFIED
        chat = Chat.objects.filter(pk=data['chat_id'], space=user.space, purpose=ChatPurposeChoice.NORMAL).first()
        if chat is None:
            raise BgmErrors.CLOSED
        if not chat.has_active_member(user):
            raise BgmErrors.CLOSED
        chat.require_user_message_allowed(user)
        with transaction.atomic():
            participation = BgmParticipation.objects.select_for_update().select_related('progress').filter(progress__campaign=campaign, progress__user=user).first()
            if participation is None:
                raise BgmErrors.CLOSED
            # The row lock serializes all entrance requests for this account/campaign.
            existing = participation.entrances.filter(request_id=request_id).first()
            if existing:
                return dict(sent=False, reason='duplicate')
            if participation.mode == 'off':
                raise BgmErrors.CLOSED
            recent = participation.entrances.filter(created_at__gte=timezone.now() - timedelta(seconds=30)).exists()
            same_chat = participation.entrances.filter(chat_id=chat.id, created_at__gte=timezone.now() - timedelta(minutes=10)).exists()
            if recent or same_chat:
                return dict(sent=False, reason='cooldown')
            points, days, _ = cls.stats(participation.progress)
            tracks = [track for track in cls.catalog(campaign) if days >= track['days'] and points >= track['points']]
            if participation.mode == 'manual':
                key = data.get('track')
                if key not in participation.slots:
                    raise BgmErrors.INVALID
            else:
                key = participation.track
                if 'track' in data:
                    raise BgmErrors.INVALID
            track = random.choice(tracks) if key == 'random' and tracks else next((track for track in tracks if track['key'] == key), None)
            if track is None:
                raise BgmErrors.CLOSED
            asset = track['asset']
            system = Message.create_system(chat, user, 'bgm_entrance', song_title=track['title'])
            resource = MediaResource.acquire(user, asset, MediaAsset.KIND_AUDIO)
            audio = Message.objects.create(chat=chat, user=user, type=MessageTypeChoice.AUDIO, media_resource=resource, content=json.dumps(dict(
                kind='audio', uri=asset.source_uri, duration_seconds=asset.duration_seconds,
                bgm_entrance=True, song_title=track['title'], song_author=track['author'], system_message_id=system.id,
            ), ensure_ascii=False))
            MessageEvent.record_created(audio)
            resource.recalculate_reference_count()
            BgmEntrance.objects.create(participation=participation, chat_id=chat.id, request_id=request_id,
                                       system_message_id=system.id, audio_message_id=audio.id)
            return dict(sent=True, audio_message_id=audio.id, uri=asset.source_uri,
                        title=track['title'], duration=asset.duration_seconds)
