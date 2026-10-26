"""Space-scoped simultaneous code-breaking rounds and server-owned growth rewards."""
import base64
import hashlib
import re
import secrets
from datetime import timedelta
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.models import ActivityCampaign, NumberDuel, NumberDuelGuess, NumberDuelReward, SpaceActivity
from Activity.presentation import presentation
from User.models import GrowthEvent, User
from User.growth import resolve_event_rule


@Error.register
class DuelErrors:
    INVALID = Error(_('Use four digits from 0 to 9; repeats are allowed'), code=Code.BadRequest)
    CLOSED = Error(_('This duel is unavailable or the activity has ended'), code=Code.BadRequest)
    FORBIDDEN = Error(_('This duel does not belong to you'), code=Code.Forbidden)
    ACCOUNT = Error(_('A verified personal account is required'), code=Code.Forbidden)
    BUSY = Error(_('Finish or leave your current duel first'), code=Code.BadRequest)
    LOCKED = Error(_('Already submitted; wait for the next round'), code=Code.BadRequest)
    TIER = Error(_('This challenge tier is not available to both players'), code=Code.BadRequest)
    FUNDS = Error(_('One player has insufficient growth points; choose a lower tier'), code=Code.BadRequest)


def validate_digits(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}', value):
        raise DuelErrors.INVALID
    return value


def feedback(secret, guess):
    exact = sum(a == b for a, b in zip(secret, guess))
    return exact, 0


def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY + ':number-duel:v1').encode()).digest()))


class NumberDuelService:
    KEY = 'number-duel-2026'
    MODE = 'number_duel'
    MAX_ROUNDS = 10
    DAILY_MATCHES = 5
    ROUND_SECONDS = 30
    ZONE = ZoneInfo('Asia/Shanghai')

    @classmethod
    def run(cls, key, user):
        run = SpaceActivity.objects.select_for_update().select_related('campaign').filter(
            campaign__key=key, campaign__enabled=True, campaign__config__mode=cls.MODE, space=user.space,
        ).first()
        if run is None:
            raise DuelErrors.CLOSED
        return run

    @staticmethod
    def day():
        return timezone.now().astimezone(NumberDuelService.ZONE).date()

    @classmethod
    def deadline(cls, run, seconds=30):
        value = timezone.now() + timedelta(seconds=seconds)
        return min(value, run.ends_at) if run.ends_at else value

    @classmethod
    def activity_payload(cls, campaign, user, run):
        return dict(
            key=campaign.key, title=campaign.title, title_en=campaign.title_en,
            summary=campaign.summary, summary_en=campaign.summary_en, theme=cls.MODE,
            starts_at=run.claimed_at.timestamp(), ends_at=run.ends_at.timestamp() if run.ends_at else None,
            active=run.is_active(), assignment_mode=campaign.assignment_mode, duration_seconds=campaign.duration_seconds,
            presentation=presentation(campaign.config), newly_claimed=not run.user_views.filter(user=user).exists(),
            verified=cls.eligible(user), today_earned=False, claimable_points=0, available_points=0,
            contributed_points=0, personal_event_count=0, personal_event_target=0, personal_reward_claimable=False,
            personal_reward=None, space_reward_claimable=None, official_user=None, space_total=0, target=0,
            milestones=[], awakenings=[],
        )

    @classmethod
    def daily(cls, user, run):
        paid = NumberDuelReward.objects.filter(user=user, duel__run=run, date=cls.day(), counted=True)
        return dict(matches=paid.count(), points=paid.aggregate(total=Sum('points'))['total'] or 0,
                    match_limit=cls.DAILY_MATCHES)

    @staticmethod
    def eligible(user):
        return bool((user.email_verified_at or user.phone_verified_at) and not user.is_official and not user.is_deleted)

    @staticmethod
    def tiers(user, refresh=False):
        level = user.calculate_growth()['level'] if refresh else min(user.growth_level, user.growth_level_cap()[0])
        return [10] + ([20] if level >= 8 else []) + ([50] if level >= 14 else [])

    @classmethod
    def finish(cls, duel, winner=None, reason='normal'):
        if duel.status == 'finished':
            return
        duel.status, duel.winner, duel.reason, duel.finished_at = 'finished', winner, reason, timezone.now()
        duel.save(update_fields=['status', 'winner', 'reason', 'finished_at'])
        if not duel.guest_id:
            return
        # Lock users in a deterministic order; both awards and quota checks share this transaction.
        people = list(User.objects.select_for_update().filter(id__in=[duel.host_id, duel.guest_id]).order_by('id'))
        # The entry date owns the quota, including matches that cross midnight.
        paid_normal = duel.funded and reason == 'normal' and all(cls.eligible(person) for person in people)
        for person in people:
            opponent_id = duel.guest_id if person.id == duel.host_id else duel.host_id
            amount, reward_reason = 0, reason
            if paid_normal and winner:
                amount = duel.stake if winner.id == person.id else -duel.stake
                if winner.id == person.id:
                    person.award_growth(f'activity:number_duel:{duel.id.hex}:{duel.stake * 2}', notify=False)
                reward_reason = 'rewarded'
            elif duel.funded:
                person.award_growth(f'activity:number_duel_refund:{duel.id.hex}:{duel.stake}', notify=False)
                reward_reason = 'rewarded' if paid_normal else reason if reason != 'normal' else 'account'
            else:
                reward_reason = duel.practice_reason or 'legacy'
            NumberDuelReward.objects.create(duel=duel, user=person, opponent_id=opponent_id,
                                           date=duel.scoring_date or cls.day(), points=amount, reason=reward_reason,
                                           counted=paid_normal)

    @classmethod
    def fund(cls, duel, stake):
        people = list(User.objects.select_for_update().filter(id__in=[duel.host_id, duel.guest_id]).order_by('id'))
        if len(people) != 2 or not all(cls.eligible(person) for person in people):
            raise DuelErrors.ACCOUNT
        # Freeze practice/paid status before taking any points from either account.
        pair = NumberDuelReward.objects.filter(duel__run=duel.run, date=cls.day(), counted=True,
                                              user_id__in=[duel.host_id, duel.guest_id],
                                              opponent_id__in=[duel.host_id, duel.guest_id]).exists()
        duel.practice_reason = 'pair' if pair else 'quota' if any(cls.daily(person, duel.run)['matches'] >= cls.DAILY_MATCHES for person in people) else ''
        if duel.practice_reason:
            return
        balances = {person.id: person.reconcile_growth() for person in people}
        if any(value < stake for value in balances.values()):
            raise DuelErrors.FUNDS
        key = f'activity:number_duel_entry:{duel.id.hex}:{stake}'
        rule = resolve_event_rule(key)
        for person in people:
            GrowthEvent.objects.create(user=person, event_key=key, category=rule.category, title=rule.title, points=rule.points)
            person.reconcile_growth()
        duel.funded, duel.scoring_date = True, cls.day()

    @classmethod
    def advance(cls, duel, timed_out=False):
        guesses = list(duel.guesses.filter(round=duel.round).select_related('user'))
        if len(guesses) < 2 and not timed_out:
            return
        solved = [row for row in guesses if row.exact == 4]
        if solved or duel.round == cls.MAX_ROUNDS:
            cls.finish(duel, solved[0].user if len(solved) == 1 else None)
            return
        if timed_out and duel.round >= 3:
            active_ids = set(duel.guesses.filter(round__gte=duel.round - 2).values_list('user_id', flat=True))
            if len(active_ids) < 2:
                cls.finish(duel, reason='inactive')
                return
        duel.round += 1
        duel.deadline = (duel.deadline + timedelta(seconds=cls.ROUND_SECONDS)) if timed_out else cls.deadline(duel.run)
        if duel.run.ends_at:
            duel.deadline = min(duel.deadline, duel.run.ends_at)
        duel.save(update_fields=['round', 'deadline'])

    @classmethod
    def expire(cls, duel):
        if duel.status == 'finished':
            return
        if not duel.run.is_active():
            cls.finish(duel, reason='ended')
        else:
            while duel.status != 'finished' and duel.deadline <= timezone.now():
                if duel.status == 'playing':
                    cls.advance(duel, timed_out=True)
                else:
                    cls.finish(duel, reason='timeout')

    @classmethod
    def current(cls, run, user):
        rows = NumberDuel.objects.filter(run=run).filter(Q(host=user) | Q(guest=user)).exclude(status='finished').select_related('run', 'host', 'guest')
        for duel in rows:
            cls.expire(duel)
            if duel.status != 'finished':
                return duel
        return None

    @staticmethod
    def read_secret(value):
        return cipher().decrypt(value.encode()).decode() if value else None

    @classmethod
    def serialize(cls, duel, user):
        if user.id not in (duel.host_id, duel.guest_id):
            raise DuelErrors.FORBIDDEN
        mine_host = user.id == duel.host_id
        own_secret = duel.host_secret if mine_host else duel.guest_secret
        other_secret = duel.guest_secret if mine_host else duel.host_secret
        other = duel.guest if mine_host else duel.host
        done = duel.status == 'finished'
        guesses = []
        for row in duel.guesses.order_by('round', 'id'):
            mine = row.user_id == user.id
            revealed = mine or row.round < duel.round or done
            guesses.append(dict(round=row.round, mine=mine, value=row.value if revealed else None,
                                exact=row.exact if revealed else None, misplaced=0 if revealed else None))
        reward = duel.rewards.filter(user=user).first() if done else None
        return dict(id=str(duel.id), code=duel.code, status=duel.status, round=duel.round,
                    stake=duel.stake, funded=duel.funded, practice_reason=duel.practice_reason,
                    balance=User.objects.values_list('growth_score', flat=True).get(id=user.id),
                    returned=(duel.stake * 2 if duel.winner_id == user.id else 0) if done and reward and reward.counted and duel.winner_id else duel.stake if done and duel.funded else 0,
                    tiers=sorted(set(cls.tiers(duel.host)) & set(cls.tiers(duel.guest))) if duel.guest else [],
                    own_tier=duel.host_stake if mine_host else duel.guest_stake,
                    opponent_tier=duel.guest_stake if mine_host else duel.host_stake,
                    deadline=duel.deadline.timestamp(), host=mine_host,
                    opponent=dict(user_id=other.id, name=other.name, **other.dictify('avatar_uri', 'avatar_cache_key')) if other else None,
                    own_secret=cls.read_secret(own_secret), opponent_locked=bool(other_secret),
                    opponent_secret=cls.read_secret(other_secret) if done else None,
                    guesses=guesses, outcome=('draw' if not duel.winner_id else 'win' if duel.winner_id == user.id else 'loss') if done else None,
                    reason=duel.reason, points=reward.points if reward else 0,
                    reward_reason=reward.reason if reward else duel.reason)

    @classmethod
    @transaction.atomic
    def lobby(cls, key, user, match_id=None, room_code=None):
        run = cls.run(key, user)
        current = cls.current(run, user)
        selected = current
        room = None
        if room_code:
            room = NumberDuel.objects.select_related('run', 'host', 'guest').filter(run=run, code=str(room_code).upper()).first()
            if room is None:
                raise DuelErrors.CLOSED
            cls.expire(room)
            selected = room if user.id in (room.host_id, room.guest_id) else None
        if match_id:
            try:
                selected = NumberDuel.objects.select_related('run', 'host', 'guest').get(id=match_id, run=run)
            except (ValueError, NumberDuel.DoesNotExist, ValidationError):
                raise DuelErrors.CLOSED
            if user.id not in (selected.host_id, selected.guest_id):
                raise DuelErrors.FORBIDDEN
            cls.expire(selected)
        history = NumberDuel.objects.filter(run=run, status='finished').filter(Q(host=user) | Q(guest=user)).select_related('host', 'guest').order_by('-finished_at')[:8]
        leaders = NumberDuelReward.objects.filter(duel__run=run).values('user_id', 'user__name').annotate(points=Sum('points')).filter(points__gt=0).order_by('-points', 'user_id')[:10]
        return dict(active=run.is_active(), ends_at=run.ends_at.timestamp() if run.ends_at else None,
                    daily=cls.daily(user, run), eligible=cls.eligible(user), tiers=cls.tiers(user),
                    max_rounds=cls.MAX_ROUNDS, round_seconds=cls.ROUND_SECONDS, server_time=timezone.now().timestamp(),
                    match=cls.serialize(selected, user) if selected else None,
                    room=cls.room_summary(room) if room else None,
                    current_code=current.code if current else None,
                    history=[dict(id=str(row.id), code=row.code, opponent=(row.guest.name if row.guest else '') if row.host_id == user.id else row.host.name,
                                  outcome='draw' if not row.winner_id else 'win' if row.winner_id == user.id else 'loss', reason=row.reason,
                                  finished_at=row.finished_at.timestamp()) for row in history],
                    leaders=[dict(user_id=row['user_id'], name=row['user__name'], points=row['points']) for row in leaders])

    @staticmethod
    def room_summary(duel):
        return dict(code=duel.code, status=duel.status, name=duel.host.name,
                    guest_name=duel.guest.name if duel.guest else None, stake=duel.stake)

    @classmethod
    @transaction.atomic
    def act(cls, key, user, data):
        run = cls.run(key, user)
        if not isinstance(data, dict):
            raise DuelErrors.INVALID
        action = data.get('action')
        if not cls.eligible(user):
            raise DuelErrors.ACCOUNT
        current = cls.current(run, user)
        if action == 'create':
            if data.get('room_flow') != 2:
                raise DuelErrors.CLOSED
            if not run.is_active() or (run.ends_at and run.ends_at - timezone.now() < timedelta(minutes=8)):
                raise DuelErrors.CLOSED
            if current:
                return cls.serialize(current, user)
            code = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(8))
            while NumberDuel.objects.filter(code=code).exists():
                code = ''.join(secrets.choice('23456789ABCDEFGHJKLMNPQRSTUVWXYZ') for _ in range(8))
            duel = NumberDuel.objects.create(run=run, host=user, code=code, deadline=cls.deadline(run, 600))
            return cls.serialize(duel, user)
        if action in ('join', 'preview'):
            code = data.get('code')
            if not isinstance(code, str) or not re.fullmatch(r'[A-Z2-9]{8}', code.upper()):
                raise DuelErrors.INVALID
            duel = NumberDuel.objects.select_related('run', 'host', 'guest').filter(code=code.upper(), run=run).first()
            if duel is None or not run.is_active():
                raise DuelErrors.CLOSED
            cls.expire(duel)
            if user.id in (duel.host_id, duel.guest_id):
                return cls.serialize(duel, user)
            if current:
                raise DuelErrors.BUSY
            if duel.status != 'waiting' or duel.guest_id:
                raise DuelErrors.CLOSED
            if not cls.eligible(duel.host):
                raise DuelErrors.ACCOUNT
            if action == 'preview':
                return dict(code=duel.code, stake=duel.stake, name=duel.host.name)
            if data.get('room_flow') != 2:
                raise DuelErrors.CLOSED
            duel.guest, duel.status, duel.stake, duel.deadline = user, 'choosing', 0, cls.deadline(run, 90)
            duel.save(update_fields=['guest', 'status', 'stake', 'deadline'])
            return cls.serialize(duel, user)
        if not isinstance(data.get('id'), str):
            raise DuelErrors.CLOSED
        try:
            duel = NumberDuel.objects.select_related('run', 'host', 'guest').get(id=data.get('id'), run=run)
        except (ValueError, NumberDuel.DoesNotExist, ValidationError):
            raise DuelErrors.CLOSED
        if user.id not in (duel.host_id, duel.guest_id):
            raise DuelErrors.FORBIDDEN
        cls.expire(duel)
        if duel.status == 'finished':
            return cls.serialize(duel, user)
        if action == 'leave':
            cls.finish(duel, (duel.guest if duel.host_id == user.id else duel.host), 'forfeit')
        elif action == 'tier':
            value = data.get('stake')
            if duel.status != 'choosing' or not duel.guest:
                raise DuelErrors.LOCKED
            people = list(User.objects.select_for_update().filter(id__in=[duel.host_id, duel.guest_id]).order_by('id'))
            allowed = set.intersection(*(set(cls.tiers(person, refresh=True)) for person in people))
            if type(value) is not int or value not in allowed:
                raise DuelErrors.TIER
            field = 'host_stake' if duel.host_id == user.id else 'guest_stake'
            setattr(duel, field, value)
            if duel.host_stake == duel.guest_stake and value in allowed:
                cls.fund(duel, value)
                duel.stake, duel.status, duel.deadline = value, 'secrets', cls.deadline(run, 60)
            duel.save(update_fields=[field, 'stake', 'status', 'deadline', 'funded', 'scoring_date', 'practice_reason'])
        elif action == 'secret':
            value = validate_digits(data.get('value'))
            field = 'host_secret' if duel.host_id == user.id else 'guest_secret'
            if getattr(duel, field):
                if cls.read_secret(getattr(duel, field)) != value:
                    raise DuelErrors.LOCKED
                return cls.serialize(duel, user)
            if duel.status != 'secrets':
                raise DuelErrors.CLOSED
            setattr(duel, field, cipher().encrypt(value.encode()).decode())
            if duel.host_secret and duel.guest_secret:
                duel.status, duel.deadline = 'playing', cls.deadline(run)
            duel.save(update_fields=[field, 'status', 'deadline'])
        elif action == 'guess':
            value = validate_digits(data.get('value'))
            round_no = data.get('round')
            if type(round_no) is not int:
                raise DuelErrors.INVALID
            existing = duel.guesses.filter(user=user, round=round_no).first()
            if existing:
                if existing.value != value:
                    raise DuelErrors.LOCKED
                return cls.serialize(duel, user)
            if duel.status != 'playing' or round_no != duel.round:
                raise DuelErrors.LOCKED
            secret = cls.read_secret(duel.guest_secret if duel.host_id == user.id else duel.host_secret)
            exact, misplaced = feedback(secret, value)
            NumberDuelGuess.objects.create(duel=duel, user=user, round=duel.round, value=value, exact=exact, misplaced=misplaced)
            cls.advance(duel)
        else:
            raise DuelErrors.INVALID
        return cls.serialize(duel, user)
