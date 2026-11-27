"""Verified, space-local tables. Hands and turn resolution stay on the server."""
import re
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.landlord_rules import beats, classify, hint
from Activity.models import LandlordSeat, LandlordTable, SpaceActivity
from Activity.presentation import presentation
from User.models import User


@Error.register
class LandlordErrors:
    CLOSED = Error(_('This space has not opened Dou Dizhu'), code=Code.Forbidden)
    ACCOUNT = Error(_('A verified personal account is required'), code=Code.Forbidden)
    TABLE = Error(_('This table is full or no longer available'), code=Code.BadRequest)
    BUSY = Error(_('Finish or leave your current table first'), code=Code.BadRequest)
    TURN = Error(_('It is not your turn'), code=Code.BadRequest)
    CARDS = Error(_('Choose a legal combination that beats the previous play'), code=Code.BadRequest)
    INVALID = Error(_('Invalid table action'), code=Code.BadRequest)


class LandlordService:
    KEY = 'landlord'
    MODE = 'landlord'
    ZONE = ZoneInfo('Asia/Shanghai')
    STATUS = LandlordTable.Status
    LIVE = (STATUS.WAITING, STATUS.BIDDING, STATUS.PLAYING)
    WAIT_LIMIT = timedelta(minutes=30)
    IDLE_LIMIT = timedelta(minutes=15)
    BID_TIME = timedelta(seconds=30)
    PLAY_TIME = timedelta(seconds=45)
    CODE_CHARSET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'

    @staticmethod
    def eligible(user):
        return user.verified and not user.is_official and not user.is_deleted and not user.is_imported_placeholder

    @classmethod
    def run(cls, key, user, lock=False):
        rows = SpaceActivity.objects
        if lock:
            rows = rows.select_for_update()
        run = rows.filter(space_id=user.space_id, campaign__key=key, campaign__enabled=True,
                          campaign__config__mode=cls.MODE).first()
        if run is None or not run.is_active():
            raise LandlordErrors.CLOSED
        return run

    @classmethod
    def activity_payload(cls, campaign, user, run):
        return dict(
            key=campaign.key, title=campaign.title, title_en=campaign.title_en, summary=campaign.summary,
            summary_en=campaign.summary_en, theme=cls.MODE, starts_at=run.claimed_at.timestamp(),
            ends_at=run.ends_at.timestamp() if run.ends_at else None,
            active=run.is_active(), assignment_mode=campaign.assignment_mode, duration_seconds=campaign.duration_seconds,
            presentation=presentation(campaign.config), newly_claimed=not run.user_views.filter(user=user).exists(),
            verified=cls.eligible(user), today_earned=False, claimable_points=0, available_points=0,
            contributed_points=0, personal_event_count=0, personal_event_target=0, personal_reward_claimable=False,
            personal_reward=None, space_reward_claimable=None, official_user=None, space_total=0, target=0,
            milestones=[], awakenings=[],
        )

    @staticmethod
    def _seats(table):
        return {seat.position: seat for seat in table.seats.select_related('user').order_by('position')}

    @staticmethod
    def _public_user(user):
        return user.dictify('user_id', 'name', 'avatar_uri')

    @classmethod
    def _table_payload(cls, table, seats, user, now):
        own = next(seat for seat in seats.values() if seat.user_id == user.pk)
        your_turn = own.position == table.turn
        best_bid = max((bid for bid in table.bids if bid is not None), default=0)
        return dict(
            code=table.code, status=table.status, version=table.version, your_seat=own.position,
            turn=table.turn, landlord=table.landlord, winner=table.winner, hand=own.hand,
            ready_count=len(seats), seats=[
                dict(position=position, member=cls._public_user(seat.user), remaining=len(seat.hand), won=seat.won)
                for position, seat in seats.items()
            ],
            bottom_cards=table.bottom_cards if table.landlord is not None else [],
            bids=table.bids, moves=table.moves, last_player=table.last_player,
            deadline=table.deadline.timestamp() if table.deadline else None,
            server_now=now.timestamp(),
            bid_options=[0, *range(best_bid + 1, 4)] if your_turn and table.status == cls.STATUS.BIDDING else [],
            can_pass=your_turn and table.status == cls.STATUS.PLAYING and bool(table.last_cards),
            suggested_cards=hint(own.hand, table.last_cards) if your_turn and table.status == cls.STATUS.PLAYING else [],
        )

    @classmethod
    def _touch(cls, table):
        table.version += 1
        table.save()

    @classmethod
    def _deal(cls, table, seats, first_bidder, now):
        deck = list(range(54))
        secrets.SystemRandom().shuffle(deck)
        for position, seat in seats.items():
            seat.hand = sorted(deck[position * 17:(position + 1) * 17])
        LandlordSeat.objects.bulk_update(seats.values(), ['hand'])
        table.status = cls.STATUS.BIDDING
        table.first_bidder = table.turn = first_bidder
        table.bottom_cards = sorted(deck[51:])
        table.bids = [None, None, None]
        table.moves = [None, None, None]
        table.deadline = now + cls.BID_TIME
        cls._touch(table)

    @classmethod
    def _bid(cls, table, seats, value, now):
        table.bids[table.turn] = value
        best = max(bid for bid in table.bids if bid is not None)
        if best == 3 or None not in table.bids:
            if best == 0:
                cls._deal(table, seats, (table.first_bidder + 1) % 3, now)
                return
            table.landlord = table.turn = table.bids.index(best)
            landlord = seats[table.landlord]
            landlord.hand = sorted([*landlord.hand, *table.bottom_cards])
            landlord.save(update_fields=['hand'])
            table.status = cls.STATUS.PLAYING
            table.deadline = now + cls.PLAY_TIME
        else:
            table.turn = (table.turn + 1) % 3
            table.deadline = now + cls.BID_TIME
        cls._touch(table)

    @classmethod
    def _play(cls, table, seats, cards, now, automatic=False):
        seat = seats[table.turn]
        if cards:
            chosen = set(cards)
            seat.hand = [card for card in seat.hand if card not in chosen]
            seat.save(update_fields=['hand'])
            table.last_cards, table.last_player = cards, seat.position
        table.moves[seat.position] = dict(cards=cards, automatic=automatic)
        if not seat.hand:
            table.status, table.winner, table.finished_at = cls.STATUS.FINISHED, seat.position, now
            table.turn = table.deadline = None
            landlord_won = seat.position == table.landlord
            for player in seats.values():
                player.won = (player.position == table.landlord) == landlord_won
            LandlordSeat.objects.bulk_update(seats.values(), ['won'])
        else:
            table.turn = (seat.position + 1) % 3
            if table.turn == table.last_player:
                table.last_cards, table.last_player = [], None
                table.moves = [None, None, None]
            table.deadline = now + cls.PLAY_TIME
        cls._touch(table)

    @classmethod
    def _abandon(cls, table, now):
        table.status, table.finished_at = cls.STATUS.ABANDONED, now
        table.turn = table.deadline = None
        cls._touch(table)

    @classmethod
    def _advance(cls, table, seats, now):
        if table.status not in cls.LIVE:
            return
        if table.status == cls.STATUS.WAITING:
            if table.updated_at <= now - cls.WAIT_LIMIT:
                cls._abandon(table, now)
            return
        if len(seats) != 3 or any(not cls.eligible(seat.user) for seat in seats.values()) or table.updated_at <= now - cls.IDLE_LIMIT:
            cls._abandon(table, now)
        elif table.deadline and table.deadline <= now:
            if table.status == cls.STATUS.BIDDING:
                cls._bid(table, seats, 0, now)
            else:
                cls._play(table, seats, [] if table.last_cards else [min(seats[table.turn].hand)], now, automatic=True)

    @classmethod
    def _active(cls, run, user):
        return LandlordTable.objects.select_for_update().filter(run=run, seats__user=user, status__in=cls.LIVE).first()

    @classmethod
    def _code(cls, value):
        if not isinstance(value, str) or not re.fullmatch('[' + cls.CODE_CHARSET + ']{8}', value.strip().upper()):
            raise LandlordErrors.TABLE
        return value.strip().upper()

    @classmethod
    def _new_table(cls, run):
        while True:
            code = ''.join(secrets.choice(cls.CODE_CHARSET) for _ in range(8))
            if not LandlordTable.objects.filter(code=code).exists():
                return LandlordTable.objects.create(run=run, code=code)

    @classmethod
    @transaction.atomic
    def lobby(cls, key, user, code=None):
        run = cls.run(key, user)
        if not cls.eligible(user):
            raise LandlordErrors.ACCOUNT
        now = timezone.now()
        tables = LandlordTable.objects.select_for_update().filter(run=run, seats__user=user)
        table = tables.filter(code=cls._code(code)).first() if code else tables.filter(status__in=cls.LIVE).first()
        current = None
        if table:
            seats = cls._seats(table)
            cls._advance(table, seats, now)
            current = cls._table_payload(table, seats, user, now)
        waiting = LandlordTable.objects.filter(run=run, status=cls.STATUS.WAITING, updated_at__gt=now - cls.WAIT_LIMIT)
        waiting = waiting.prefetch_related(Prefetch(
            'seats', queryset=LandlordSeat.objects.select_related('user').defer('hand').order_by('position'),
        )).order_by('-updated_at')[:30]
        rooms = []
        for room in waiting:
            members = [seat.user for seat in room.seats.all() if cls.eligible(seat.user)]
            if members:
                rooms.append(dict(code=room.code, host=cls._public_user(members[0]), count=len(members)))
        return dict(table=current, rooms=rooms)

    @classmethod
    @transaction.atomic
    def act(cls, key, user, data):
        if not isinstance(data, dict):
            raise LandlordErrors.INVALID
        run = cls.run(key, user, lock=True)
        if not cls.eligible(user):
            raise LandlordErrors.ACCOUNT
        action = data.get('action')
        now = timezone.now()
        if action in ('create', 'join'):
            code = cls._code(data.get('code')) if action == 'join' else None
            current = cls._active(run, user)
            if current:
                seats = cls._seats(current)
                cls._advance(current, seats, now)
                if current.status in cls.LIVE:
                    if code and code != current.code:
                        raise LandlordErrors.BUSY
                    return dict(table=cls._table_payload(current, seats, user, now))
            table = (LandlordTable.objects.select_for_update().filter(run=run, code=code).first()
                     if code else cls._new_table(run))
            if table is None or table.status != cls.STATUS.WAITING or table.updated_at <= now - cls.WAIT_LIMIT:
                raise LandlordErrors.TABLE
            seats = cls._seats(table)
            for position in [index for index, seat in seats.items() if not cls.eligible(seat.user)]:
                seats.pop(position).delete()
            if len(seats) >= 3:
                raise LandlordErrors.TABLE
            position = min(set(range(3)) - set(seats))
            seats[position] = LandlordSeat.objects.create(table=table, user=user, position=position)
            if len(seats) == 3:
                cls._deal(table, seats, secrets.randbelow(3), now)
            else:
                cls._touch(table)
            return dict(table=cls._table_payload(table, seats, user, now))
        if action not in ('bid', 'play', 'pass', 'leave'):
            raise LandlordErrors.INVALID
        table = LandlordTable.objects.select_for_update().filter(run=run, seats__user=user, code=cls._code(data.get('code'))).first()
        if table is None:
            raise LandlordErrors.TABLE
        seats = cls._seats(table)
        own = next(seat for seat in seats.values() if seat.user_id == user.pk)
        cls._advance(table, seats, now)
        if type(data.get('version')) is not int or table.version != data['version'] or table.status not in cls.LIVE:
            return dict(table=cls._table_payload(table, seats, user, now), changed=True)
        if action == 'leave':
            if table.status == cls.STATUS.WAITING:
                own.delete()
                if len(seats) == 1:
                    cls._abandon(table, now)
                else:
                    cls._touch(table)
            else:
                cls._abandon(table, now)
            return dict(table=None)
        if table.turn != own.position:
            raise LandlordErrors.TURN
        if action == 'bid' and table.status == cls.STATUS.BIDDING:
            value = data.get('value')
            high = max((bid for bid in table.bids if bid is not None), default=0)
            if type(value) is not int or (value != 0 and not high < value <= 3):
                raise LandlordErrors.INVALID
            cls._bid(table, seats, value, now)
        elif action == 'pass' and table.status == cls.STATUS.PLAYING and table.last_cards:
            cls._play(table, seats, [], now)
        elif action == 'play' and table.status == cls.STATUS.PLAYING:
            cards = data.get('cards')
            if (not isinstance(cards, list) or not beats(classify(cards), classify(table.last_cards))
                    or not set(cards).issubset(own.hand)):
                raise LandlordErrors.CARDS
            cls._play(table, seats, sorted(cards), now)
        else:
            raise LandlordErrors.INVALID
        return dict(table=cls._table_payload(table, seats, user, now))

    @classmethod
    def leaderboard(cls, key, user, year):
        run = cls.run(key, user)
        now_year = timezone.now().astimezone(cls.ZONE).year
        first_year = run.claimed_at.astimezone(cls.ZONE).year
        try:
            year = int(year) if year else now_year
        except (TypeError, ValueError):
            raise LandlordErrors.INVALID
        if not first_year <= year <= now_year:
            raise LandlordErrors.INVALID
        rows = list(LandlordSeat.objects.filter(
            table__run=run, table__status=cls.STATUS.FINISHED, user__is_deleted=False,
            table__finished_at__gte=datetime(year, 1, 1, tzinfo=cls.ZONE),
            table__finished_at__lt=datetime(year + 1, 1, 1, tzinfo=cls.ZONE),
        ).values('user_id').annotate(games=Count('pk'), wins=Count('pk', filter=Q(won=True)))
            .order_by('-wins', 'games', 'user_id'))
        members = User.objects.only('id', 'name', 'avatar_type', 'avatar_uri').in_bulk([row['user_id'] for row in rows[:50]])
        top, mine, place, previous = [], None, 0, None
        for position, row in enumerate(rows, 1):
            score = (row['wins'], row['games'] if row['wins'] else 0)
            if score != previous:
                previous, place = score, position
            result = dict(rank=place, wins=row['wins'], games=row['games'])
            if row['user_id'] == user.pk:
                mine = result
            if row['user_id'] in members:
                top.append(dict(**result, member=cls._public_user(members[row['user_id']])))
        return dict(year=year, years=list(range(now_year, first_year - 1, -1)), rows=top, mine=mine)
