"""Two-deck partnership game, including the big-joker and star rules of Firefight."""
from collections import Counter
from dataclasses import dataclass
from itertools import combinations_with_replacement
from secrets import SystemRandom

from Activity.card_game import automatic_play, card_rank, consecutive, deal, group_by_rank, next_position, valid_cards


MODES = ('classic', 'firefight')
RUNS = ((1, 5, 'straight'), (2, 3, 'pairs'), (3, 3, 'triples'))


@dataclass(frozen=True)
class Pattern:
    kind: str
    high: int
    size: int
    stars: int = 0
    special: int = 0
    ranks: tuple = ()

    @property
    def strength(self):
        return self.stars, self.special, -self.size, self.high


def _formed(ranks, firefight):
    counts = Counter(ranks)
    ordered = sorted(counts)
    size = len(ranks)
    high = ordered[-1]
    if len(ordered) == 1:
        if size <= 3:
            return Pattern(('single', 'pair', 'triple')[size - 1], high, size, ranks=tuple(ranks))
        if high <= 12 and size <= 8:
            return Pattern('bomb', high, size, size, ranks=tuple(ranks))
    for copies, minimum, kind in RUNS:
        if consecutive(ordered, minimum) and all(count == copies for count in counts.values()):
            return Pattern(kind, high, size, ranks=tuple(ranks))
    copies = counts[high]
    if (firefight and 4 <= copies <= 8 and len(ordered) <= 6 and consecutive(ordered, 3)
            and all(count == copies for count in counts.values())):
        return Pattern('chain', high, size, copies + len(ordered), ranks=tuple(ranks))
    return None


def classify(cards, mode):
    if not valid_cards(cards, decks=2, maximum=27):
        return None
    ranks = tuple(map(card_rank, cards))
    firefight = mode == 'firefight'
    if all(rank >= 13 for rank in ranks):
        if len(cards) == 4:
            return Pattern('royal', 14, 4, 7 if firefight else 9, 1, ranks)
        if firefight and len(cards) == 3:
            return Pattern('three_jokers', 14, 3, 6, -1, ranks)
    if not firefight or len(cards) < 2 or 14 not in ranks:
        return _formed(ranks, firefight)

    # Wild jokers may still play at face value. Try each assignment once, then keep the strongest legal reading.
    wild_indices = [index for index, rank in enumerate(ranks) if rank == 14]
    best = None
    assignments = ([(rank,) for rank in range(15)] if len(wild_indices) == 1
                   else ((first, second) for first, second in combinations_with_replacement(range(15), 2)))
    for assignment in assignments:
        resolved = list(ranks)
        for index, rank in zip(wild_indices, assignment):
            resolved[index] = rank
        candidate = _formed(resolved, firefight)
        if candidate is not None and (best is None or candidate.strength > best.strength):
            best = candidate
    return best


def beats(candidate, previous):
    if candidate is None:
        return False
    if previous is None:
        return True
    if candidate.stars or previous.stars:
        return candidate.strength > previous.strength
    return candidate.kind == previous.kind and candidate.size == previous.size and candidate.high > previous.high


def _match(groups, wild, ranks, count):
    cards = [card for rank in ranks for card in groups[rank][:count]]
    missing = len(ranks) * count - len(cards)
    return cards + wild[:missing] if missing <= len(wild) else []


def hint(hand, previous, mode):
    if not hand:
        return []
    if beats(classify(hand, mode), previous):
        return hand
    groups = group_by_rank(hand)
    wild = groups.pop(14, []) if mode == 'firefight' else []
    ordinary = []
    for copies, minimum, kind in RUNS:
        lengths = (range(min(12, len(hand) // copies), minimum - 1, -1) if previous is None
                   else (previous.size // copies,) if previous.kind == kind else ())
        for length in lengths:
            if length >= minimum:
                ordinary.extend(_match(groups, wild, tuple(range(start, start + length)), copies)
                                for start in range(13 - length))
    for copies, kind in ((3, 'triple'), (2, 'pair'), (1, 'single')):
        if previous is None or previous.kind == kind:
            ordinary.extend(_match(groups, wild, (rank,), copies) for rank in range(15))
    options = [(len(cards), pattern.high, cards) for cards in ordinary if cards
               and (pattern := classify(cards, mode)) and not pattern.stars and beats(pattern, previous)]
    if options:
        return min(options, key=lambda choice: (-choice[0], choice[1]))[2]

    bombs = [_match(groups, wild, (rank,), count) for count in range(4, 9) for rank in range(13)]
    jokers = groups[13] + (wild or groups[14])
    if len(jokers) == 4:
        bombs.append(jokers)
    if mode == 'firefight':
        if len(jokers) >= 3:
            bombs.append(jokers[:3])
        for length in range(3, 7):
            for copies in range(4, min(8, len(hand) // length) + 1):
                bombs.extend(_match(groups, wild, tuple(range(start, start + length)), copies)
                             for start in range(13 - length))
    choices = [(pattern.strength, cards) for cards in bombs if cards and (pattern := classify(cards, mode))
               and beats(pattern, previous)]
    return min(choices, default=(None, []))[1]


def start(mode, _host):
    hands, _ = deal(players=4, cards_per_player=27, decks=2)
    rng = SystemRandom()
    first = rng.randrange(4)
    opening = None
    if mode == 'firefight':
        candidates = [(position, card, partner) for position, hand in enumerate(hands) for card in hand
                      for partner, other in enumerate(hands) if partner != position and (card + 54) % 108 in other]
        first, opening, partner = rng.choice(candidates)
        opposite = (first + 2) % 4
        hands[opposite], hands[partner] = hands[partner], hands[opposite]
    return dict(hands=hands, turn=first, opening=opening, last=[], last_player=None, last_pattern=None,
                passed=[], placements=[], stars=[0, 0], contributions=[0] * 4, scores=None, winners=[], outcome=None)


def _settle(board, mode):
    placements = board['placements']
    team = placements[0] % 2
    partner = (placements[0] + 2) % 4
    if partner not in placements:
        placements.append(partner)
    place = placements.index(partner)
    outcome = ('double', 'single', 'even')[place - 1]
    base = (4, 2, 0)[place - 1] if mode == 'firefight' else (3, 2, 1)[place - 1]
    stars = board['stars'][team]
    multiplier = 2 ** min(4, max(0, stars - 4)) if mode == 'firefight' else 1
    winners = [position for position in range(4) if position % 2 == team] if base else []
    board.update(outcome=outcome, winners=winners, turn=None)
    board['scores'] = [((1 if position in winners else -1) * base * multiplier + board['contributions'][position])
                       for position in range(4)]
    placements.extend(sorted(set(range(4)) - set(placements), key=lambda position: (len(board['hands'][position]), position)))


def play(board, position, cards, mode):
    if board['turn'] != position:
        raise ValueError('turn')
    hand = board['hands'][position]
    if cards:
        pattern = classify(cards, mode)
        if not pattern or not set(cards).issubset(hand) or not beats(pattern, classify(board['last'], mode)):
            raise ValueError('cards')
        played = set(cards)
        board['hands'][position] = [card for card in hand if card not in played]
        board.update(last=cards, last_player=position, passed=[],
                     last_pattern=dict(kind=pattern.kind, ranks=list(pattern.ranks), stars=pattern.stars))
        board['stars'][position % 2] = max(board['stars'][position % 2], pattern.stars)
        if mode == 'firefight' and pattern.stars >= 6:
            value = min(pattern.stars - 5, 3) * 2
            for seat in range(4):
                board['contributions'][seat] += value * (3 if seat == position else -1)
        if not board['hands'][position]:
            board['placements'].append(position)
            if (position + 2) % 4 in board['placements']:
                _settle(board, mode)
                return
    else:
        if not board['last']:
            raise ValueError('cards')
        board['passed'].append(position)

    remaining = [seat for seat in range(4) if board['hands'][seat]]
    leader = board['last_player']
    if all(seat == leader or seat in board['passed'] for seat in remaining):
        following = leader if leader in remaining else (leader + 2) % 4
        board.update(last=[], last_player=None, last_pattern=None, passed=[])
    else:
        following = next_position(position, 4)
    while following not in remaining:
        following = next_position(following, 4)
    board['turn'] = following


def automatic(board, mode):
    position = board['turn']
    previous = classify(board['last'], mode)
    leader = board['last_player']
    play(board, position, automatic_play(
        board['hands'][position], previous, lambda hand, prior: hint(hand, prior, mode),
        teammate_leads=leader is not None and leader % 2 == position % 2,
        save=lambda cards: bool(cards and classify(cards, mode).stars),
    ), mode)
