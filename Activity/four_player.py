"""Shared server-owned room lifecycle for the four-player activities."""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Prefetch
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity import mahjong, shuangkou
from Activity.models import FourPlayerResult, FourPlayerSeat, FourPlayerTable
from Activity.table_activity import (BETWEEN_ROUNDS, FOUR_PLAYER_MODES, ROOM_WAIT, activity_run, eligible,
                                     forfeit_scores, new_room, public_user, room_code)


@Error.register
class FourPlayerErrors:
    CLOSED = Error(_('This space has not opened this game'), code=Code.Forbidden)
    ACCOUNT = Error(_('A verified personal account is required'), code=Code.Forbidden)
    TABLE = Error(_('This table is full or no longer available'), code=Code.BadRequest)
    BUSY = Error(_('Finish or leave your current table first'), code=Code.BadRequest)
    TURN = Error(_('It is not your turn'), code=Code.BadRequest)
    CARDS = Error(_('Choose a legal play'), code=Code.BadRequest)
    INVALID = Error(_('Invalid table action'), code=Code.BadRequest)


ENGINES = {'shuangkou': shuangkou, 'mahjong': mahjong}
LIVE = (*BETWEEN_ROUNDS, 'playing')
WAIT = timedelta(seconds=ROOM_WAIT)
IDLE = timedelta(minutes=15)
TURN = timedelta(seconds=35)
CALL = timedelta(seconds=8)
AUTOMATIC = timedelta(seconds=2)


def _seats(table):
    return {seat.position: seat for seat in table.seats.select_related('user').order_by('position')}


def _touch(table):
    table.version += 1
    table.save()


def _run(key, user, lock=False):
    run = activity_run(key, user, FOUR_PLAYER_MODES, lock=lock)
    if not run:
        raise FourPlayerErrors.CLOSED
    if not eligible(user):
        raise FourPlayerErrors.ACCOUNT
    return run


def _code(value):
    code = room_code(value)
    if code is None:
        raise FourPlayerErrors.TABLE
    return code


def _can_start(seats):
    return len(seats) == 4 and all(seat.ready and eligible(seat.user) for seat in seats.values())


def _public_board(key, table, own):
    board = table.board
    if not board:
        return None
    playing = table.status == 'playing'
    same_player = board['players'][own.position] == own.user_id
    hand = board['hands'][own.position] if same_player else []
    common = dict(hand=hand, turn=board['turn'], winners=board['winners'], outcome=board['outcome'],
                  forfeited_position=board.get('forfeited_position'))
    if key == 'shuangkou':
        previous = shuangkou.classify(board['last'], table.mode) if playing else None
        can_play = playing and board['turn'] == own.position and not own.trustee
        return dict(**common, last=board['last'], last_player=board['last_player'], last_pattern=board['last_pattern'],
                    can_pass=can_play and bool(board['last']), passed=board['passed'],
                    suggested=shuangkou.hint(hand, previous, table.mode) if can_play else [],
                    placements=board['placements'], opening=board['opening'], contributions=board['contributions'])
    pending = board['pending']
    own_key = str(own.position)
    choices = None
    if pending and own_key in pending['offers'] and own_key not in pending['votes'] and not own.trustee:
        choices = pending['offers'][own_key]
    visible_melds = [[dict(kind=meld['kind'], tiles=(meld['tiles'] if position == own.position and same_player
                      or meld['kind'] != 'concealed_kong' else [None] * 4)) for meld in melds]
                     for position, melds in enumerate(board['melds'])]
    return dict(**common, drawn=board['drawn'] if board['turn'] == own.position else None, dealer=board['dealer'],
                wall_count=len(board['wall']), rivers=board['rivers'], melds=visible_melds,
                own_choices=mahjong.own_choices(board, own.position) if playing and not own.trustee else [],
                pending=dict(source=pending['source'], tile=pending['tile'], kind=pending['kind'],
                             decision=board['decision'], choices=choices) if pending else None)


def _snapshot(key, table, seats, user, now):
    own = next(seat for seat in seats.values() if seat.user_id == user.pk)
    board = table.board
    return dict(code=table.code, status=table.status, mode=table.mode, version=table.version, round=table.round_number,
                host=table.host_position, your_seat=own.position, server_now=now.timestamp(),
                deadline=table.deadline.timestamp() if table.deadline else None,
                can_start=table.status in BETWEEN_ROUNDS and own.position == table.host_position and _can_start(seats),
                seats=[dict(position=seat.position, member=public_user(seat.user), ready=seat.ready, trustee=seat.trustee,
                            remaining=len(board['hands'][seat.position]) if board and board['players'][seat.position] == seat.user_id else 0,
                            score=board['scores'][seat.position] if board and board['scores'] is not None
                            and board['players'][seat.position] == seat.user_id else None) for seat in seats.values()],
                board=_public_board(key, table, own))


def _schedule(table, seats, now):
    board = table.board
    if board.get('pending'):
        table.deadline = now + CALL
    else:
        table.deadline = now + (AUTOMATIC if seats[board['turn']].trustee else TURN)


def _resolve_move(table, seats, now, previous_decision=None):
    board = table.board
    if board['scores'] is not None:
        table.status, table.deadline = 'finished', None
        FourPlayerResult.objects.bulk_create([
            FourPlayerResult(table=table, user_id=seat.user_id, round_number=table.round_number,
                             mode=table.mode, score=board['scores'][seat.position],
                             won=seat.position in board['winners'], finished_at=now) for seat in seats.values()
            if board['outcome'] != 'forfeit' or board['scores'][seat.position] != 0
        ])
    elif not board.get('pending') or board['decision'] != previous_decision:
        _schedule(table, seats, now)
    _touch(table)


def _close(table):
    table.status, table.deadline, table.board = 'abandoned', None, {}
    _touch(table)


def _tick(key, table, seats, now):
    if table.status in BETWEEN_ROUNDS and table.updated_at <= now - WAIT:
        _close(table)
    if table.status != 'playing':
        return
    if table.updated_at <= now - IDLE or len(seats) != 4 or any(not eligible(seat.user) for seat in seats.values()):
        _close(table)
    elif table.deadline and table.deadline <= now:
        turn = table.board['turn']
        if turn is not None and not seats[turn].trustee:
            seats[turn].trustee = True
            seats[turn].save(update_fields=['trustee'])
        if key == 'shuangkou':
            shuangkou.automatic(table.board, table.mode)
        else:
            mahjong.automatic(table.board)
        _resolve_move(table, seats, now)


def _active(run, user):
    return FourPlayerTable.objects.select_for_update().filter(run=run, seats__user=user, status__in=LIVE).first()


@transaction.atomic
def lobby(key, user, code=None):
    run = _run(key, user)
    mode = run.campaign.config['mode']
    now = timezone.now()
    if code:
        table = FourPlayerTable.objects.select_for_update().filter(run=run, seats__user=user, code=_code(code)).first()
    else:
        table = _active(run, user)
    current = None
    if table:
        seats = _seats(table)
        _tick(mode, table, seats, now)
        current = _snapshot(mode, table, seats, user, now)
    rooms = []
    if not current or current['status'] == 'abandoned':
        available = FourPlayerTable.objects.filter(run=run, status__in=BETWEEN_ROUNDS, updated_at__gt=now - WAIT)
        available = available.annotate(seat_count=Count('seats')).filter(seat_count__gt=0, seat_count__lt=4).defer('board')
        available = available.prefetch_related(Prefetch('seats', queryset=FourPlayerSeat.objects.select_related('user').order_by('position')))
        for room in available.order_by('-updated_at')[:30]:
            members = {seat.position: seat.user for seat in room.seats.all() if eligible(seat.user)}
            if members:
                rooms.append(dict(code=room.code, mode=room.mode, count=len(members),
                                  host=public_user(members.get(room.host_position, next(iter(members.values()))))))
    return dict(table=current, rooms=rooms)


def _join(key, run, user, code, now):
    current = _active(run, user)
    if current:
        seats = _seats(current)
        _tick(key, current, seats, now)
        if current.status in LIVE:
            if code and code != current.code:
                raise FourPlayerErrors.BUSY
            return current, seats
    table = (FourPlayerTable.objects.select_for_update().filter(run=run, code=code).first()
             if code else new_room(FourPlayerTable, run=run, mode=ENGINES[key].MODES[0]))
    if not table or table.status not in BETWEEN_ROUNDS or table.updated_at <= now - WAIT:
        raise FourPlayerErrors.TABLE
    seats = _seats(table)
    for position in [item for item, seat in seats.items() if not eligible(seat.user)]:
        seats.pop(position).delete()
    if len(seats) == 4:
        raise FourPlayerErrors.TABLE
    position = min(set(range(4)) - set(seats))
    seats[position] = FourPlayerSeat.objects.create(table=table, user=user, position=position)
    if table.host_position not in seats:
        table.host_position = min(seats)
    _touch(table)
    return table, seats


def _start(key, table, seats, now):
    board = table.board
    dealer = board['winners'][0] if board.get('winners') else board.get('dealer', table.host_position)
    table.board = ENGINES[key].start(table.mode, dealer)
    table.board['players'] = [seats[position].user_id for position in range(4)]
    table.status = 'playing'
    table.round_number += 1
    for seat in seats.values():
        seat.ready = seat.trustee = False
    FourPlayerSeat.objects.bulk_update(seats.values(), ['ready', 'trustee'])
    _schedule(table, seats, now)
    _touch(table)


@transaction.atomic
def act(key, user, data):
    run = _run(key, user, lock=True)
    key = run.campaign.config['mode']
    if not isinstance(data, dict):
        raise FourPlayerErrors.INVALID
    action = data.get('action')
    now = timezone.now()
    if action in ('create', 'join'):
        table, seats = _join(key, run, user, _code(data.get('code')) if action == 'join' else None, now)
        return dict(table=_snapshot(key, table, seats, user, now))
    table = FourPlayerTable.objects.select_for_update().filter(run=run, code=_code(data.get('code')), seats__user=user).first()
    if not table:
        raise FourPlayerErrors.TABLE
    seats = _seats(table)
    own = next(seat for seat in seats.values() if seat.user_id == user.pk)
    _tick(key, table, seats, now)
    if (table.status not in LIVE or type(data.get('version')) is not int or data.get('round') != table.round_number
            or (action == 'ready' and data.get('mode', table.mode) != table.mode)
            or (action == 'claim' and (table.status != 'playing' or not table.board.get('pending')
                                      or data.get('decision') != table.board['decision']))
            or (action not in ('claim', 'ready', 'trustee', 'leave') and data['version'] != table.version)):
        return dict(table=_snapshot(key, table, seats, user, now), changed=True)
    position = own.position
    value = data.get('value')
    if action == 'leave':
        if table.status == 'playing':
            opponents = [seat for seat in seats if seat != position and (key == 'mahjong' or seat % 2 != position % 2)]
            scores = forfeit_scores(seats, position, opponents)
            table.board.update(outcome='forfeit', forfeited_position=position, turn=None, pending=None,
                               winners=opponents, scores=[scores[seat] for seat in range(4)])
            _resolve_move(table, seats, now)
        own.delete()
        seats.pop(position)
        if not seats:
            _close(table)
        else:
            if table.host_position == position:
                table.host_position = min(seats)
            _touch(table)
        return dict(table=None)
    if table.status in BETWEEN_ROUNDS:
        if action == 'ready' and type(value) is bool:
            own.ready = value
            own.save(update_fields=['ready'])
            _touch(table)
        elif action == 'start' and position == table.host_position and _can_start(seats):
            _start(key, table, seats, now)
        elif action == 'mode' and position == table.host_position and value in ENGINES[key].MODES:
            if table.mode != value:
                table.mode = value
                table.seats.update(ready=False)
                for seat in seats.values():
                    seat.ready = False
                _touch(table)
        else:
            raise FourPlayerErrors.INVALID
    elif action == 'trustee' and type(value) is bool:
        own.trustee = value
        own.save(update_fields=['trustee'])
        if table.board['turn'] == position:
            _schedule(table, seats, now)
        _touch(table)
    elif own.trustee:
        raise FourPlayerErrors.TURN
    else:
        _game_action(key, table, seats, position, action, data, now)
    return dict(table=_snapshot(key, table, seats, user, now))


def _game_action(key, table, seats, position, action, data, now):
    board = table.board
    previous_decision = board.get('decision') if board.get('pending') else None
    try:
        if key == 'shuangkou' and action in ('play', 'pass'):
            cards = data.get('cards') if action == 'play' else []
            if not isinstance(cards, list) or (action == 'play' and not cards):
                raise ValueError('cards')
            shuangkou.play(board, position, cards, table.mode)
        elif key == 'mahjong' and action == 'discard':
            mahjong.discard(board, position, data.get('tile'))
        elif key == 'mahjong' and action == 'mahjong_action':
            mahjong.own_action(board, position, data.get('index'))
        elif key == 'mahjong' and action == 'claim':
            mahjong.vote(board, position, data.get('index'), data.get('decision'))
        else:
            raise FourPlayerErrors.INVALID
    except ValueError as error:
        raise FourPlayerErrors.TURN if str(error) == 'turn' else FourPlayerErrors.CARDS
    _resolve_move(table, seats, now, previous_decision)
