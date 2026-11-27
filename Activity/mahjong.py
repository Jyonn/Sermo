"""Four-player 136-tile mahjong. The wall and unclaimed choices stay on the server."""
from collections import Counter
from functools import lru_cache
from secrets import SystemRandom

from Activity.card_game import next_position


MODES = ('standard',)
ORPHANS = {0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33}
PRIORITY = {'hu': 3, 'pong': 2, 'kong': 2, 'chi': 1}


def face(tile):
    return tile // 4


@lru_cache(maxsize=8192)
def _sets(counts):
    first = next((index for index, count in enumerate(counts) if count), None)
    if first is None:
        return True
    if counts[first] >= 3:
        rest = list(counts)
        rest[first] -= 3
        if _sets(tuple(rest)):
            return True
    if first < 27 and first % 9 < 7 and counts[first + 1] and counts[first + 2]:
        rest = list(counts)
        for index in range(first, first + 3):
            rest[index] -= 1
        return _sets(tuple(rest))
    return False


def winning(hand, exposed=0):
    if len(hand) != 14 - 3 * exposed:
        return False
    counts = Counter(map(face, hand))
    if any(count > 4 for count in counts.values()):
        return False
    if exposed == 0 and (sum(count // 2 for count in counts.values()) == 7 or set(counts) == ORPHANS):
        return True
    ordered = tuple(counts[index] for index in range(34))
    for index, count in counts.items():
        if count >= 2:
            rest = list(ordered)
            rest[index] -= 2
            if _sets(tuple(rest)):
                return True
    return False


def start(_mode, dealer):
    wall = list(range(136))
    SystemRandom().shuffle(wall)
    hands = [sorted(wall[index:index + 13]) for index in range(0, 52, 13)]
    board = dict(hands=hands, dealer=dealer, turn=dealer, rivers=[[] for _ in range(4)], melds=[[] for _ in range(4)],
                 wall=wall[52:], pending=None, decision=0, drawn=None, winners=[], scores=None, outcome=None)
    _draw(board, dealer)
    return board


def _settle(board, winner=None, source=None, outcome='draw'):
    scores = [0] * 4
    if winner is not None:
        scores = [-1] * 4 if source is None else [0] * 4
        if source is not None:
            scores[source] = -3
        scores[winner] = 3
    board.update(turn=None, winners=[] if winner is None else [winner], scores=scores, outcome=outcome)


def _draw(board, position, replacement=False):
    wall = board['wall']
    if not wall:
        _settle(board)
        return
    tile = wall.pop(0 if replacement else -1)
    board['hands'][position].append(tile)
    board['hands'][position].sort()
    board.update(turn=position, drawn=tile)


def _claims(board, position, source, tile, kong=False):
    hand = board['hands'][position]
    melds = board['melds'][position]
    options = []
    if winning([*hand, tile], len(melds)):
        options.append(dict(kind='hu'))
    if kong:
        return options
    rank = face(tile)
    counts = Counter(map(face, hand))
    if counts[rank] >= 3 and board['wall']:
        options.append(dict(kind='kong', faces=[rank] * 3))
    if counts[rank] >= 2:
        options.append(dict(kind='pong', faces=[rank] * 2))
    if position == next_position(source, 4) and rank < 27:
        suit = rank // 9 * 9
        for first in range(max(suit, rank - 2), min(rank, suit + 6) + 1):
            others = [value for value in range(first, first + 3) if value != rank]
            if all(counts[value] for value in others):
                options.append(dict(kind='chi', faces=others))
    return options


def _open_claim(board, position, tile, kind, meld=None):
    offers = {}
    for seat in range(4):
        if seat != position and (choices := _claims(board, seat, position, tile, kind == 'added_kong')):
            offers[str(seat)] = choices
    board['decision'] += 1
    board['pending'] = dict(source=position, tile=tile, kind=kind, meld=meld, offers=offers, votes={})
    board.update(turn=None, drawn=None)
    if not offers:
        settle_claims(board)


def own_choices(board, position):
    if board['turn'] != position or board['pending']:
        return []
    hand = board['hands'][position]
    melds = board['melds'][position]
    options = [dict(kind='hu')] if winning(hand, len(melds)) else []
    if board['wall']:
        counts = Counter(map(face, hand))
        options.extend(dict(kind='concealed_kong', face=rank) for rank, count in counts.items() if count == 4)
        options.extend(dict(kind='added_kong', face=face(meld['tiles'][0])) for meld in melds
                       if meld['kind'] == 'pong' and counts[face(meld['tiles'][0])])
    return options


def _take(hand, faces):
    tiles = []
    for rank in faces:
        tile = next(tile for tile in hand if face(tile) == rank)
        hand.remove(tile)
        tiles.append(tile)
    return tiles


def discard(board, position, tile):
    if board['turn'] != position or board['pending']:
        raise ValueError('turn')
    hand = board['hands'][position]
    if type(tile) is not int or tile not in hand:
        raise ValueError('cards')
    hand.remove(tile)
    board['rivers'][position].append(tile)
    _open_claim(board, position, tile, 'discard')


def own_action(board, position, index):
    choices = own_choices(board, position)
    if type(index) is not int or not 0 <= index < len(choices):
        raise ValueError('cards')
    option = choices[index]
    kind = option['kind']
    if kind == 'hu':
        _settle(board, position, outcome='self_draw')
        return
    rank = option['face']
    hand = board['hands'][position]
    melds = board['melds'][position]
    if kind == 'concealed_kong':
        melds.append(dict(kind=kind, tiles=_take(hand, [rank] * 4)))
        _draw(board, position, replacement=True)
        return
    slot = next(index for index, meld in enumerate(melds) if meld['kind'] == 'pong' and face(meld['tiles'][0]) == rank)
    tile = _take(hand, [rank])[0]
    _open_claim(board, position, tile, 'added_kong', slot)


def vote(board, position, index, decision):
    pending = board['pending']
    key = str(position)
    if not pending or decision != board['decision'] or key not in pending['offers']:
        raise ValueError('turn')
    options = pending['offers'][key]
    if type(index) is not int or not -1 <= index < len(options):
        raise ValueError('cards')
    pending['votes'].setdefault(key, index)
    if len(pending['votes']) == len(pending['offers']):
        settle_claims(board)


def settle_claims(board):
    pending = board.pop('pending')
    board['pending'] = None
    source, tile = pending['source'], pending['tile']
    claims = [(int(seat), pending['offers'][seat][index]) for seat, index in pending['votes'].items() if index >= 0]
    if not claims:
        if pending['kind'] == 'added_kong':
            meld = board['melds'][source][pending['meld']]
            meld['kind'] = 'added_kong'
            meld['tiles'].append(tile)
            _draw(board, source, replacement=True)
        else:
            _draw(board, next_position(source, 4))
        return
    position, option = max(claims, key=lambda claim: (PRIORITY[claim[1]['kind']], -((claim[0] - source) % 4)))
    kind = option['kind']
    if pending['kind'] == 'discard':
        board['rivers'][source].pop()
    if kind == 'hu':
        board['hands'][position].append(tile)
        board['hands'][position].sort()
        _settle(board, position, source, 'rob_kong' if pending['kind'] == 'added_kong' else 'discard_win')
        return
    tiles = _take(board['hands'][position], option['faces'])
    board['melds'][position].append(dict(kind=kind, tiles=sorted([*tiles, tile]), source=source))
    if kind == 'kong':
        _draw(board, position, replacement=True)
    else:
        board.update(turn=position, drawn=None)


def automatic(board):
    if board['pending']:
        settle_claims(board)
        return
    position = board['turn']
    options = own_choices(board, position)
    if options and options[0]['kind'] == 'hu':
        own_action(board, position, 0)
    else:
        hand = board['hands'][position]
        discard(board, position, board['drawn'] if board['drawn'] is not None else hand[-1])
