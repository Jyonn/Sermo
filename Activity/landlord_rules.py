"""Card identifiers and the classic three-player Dou Dizhu combinations."""
from collections import Counter, defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class Combination:
    kind: str
    high: int
    size: int
    length: int = 1


def rank(card):
    return card // 4 if card < 52 else card - 39


def classify(cards):
    if (not cards or len(cards) > 20 or any(type(card) is not int or not 0 <= card < 54 for card in cards)
            or len(set(cards)) != len(cards)):
        return None
    counts = Counter(map(rank, cards))
    size = len(cards)
    ranks = sorted(counts)
    if ranks == [13, 14] and size == 2:
        return Combination('rocket', 14, 2)
    if len(ranks) == 1:
        return Combination({1: 'single', 2: 'pair', 3: 'triple', 4: 'bomb'}[size], ranks[0], size)

    for copies, minimum, kind in ((1, 5, 'straight'), (2, 3, 'pairs'), (3, 2, 'plane')):
        if len(ranks) >= minimum and ranks[-1] < 12 and ranks[-1] - ranks[0] + 1 == len(ranks):
            if all(counts[item] == copies for item in ranks):
                return Combination(kind, ranks[-1], size, len(ranks))

    for copies, wings, unit, kind in (
        (3, 1, 1, 'triple_single'), (3, 1, 2, 'triple_pair'),
        (4, 2, 1, 'four_singles'), (4, 2, 2, 'four_pairs'),
    ):
        if size != copies + wings * unit:
            continue
        for main in ranks:
            if counts[main] == copies and _wings_match([counts[r] for r in ranks if r != main], wings, unit):
                return Combination(kind, main, size)

    for unit, kind in ((1, 'plane_singles'), (2, 'plane_pairs')):
        length, remainder = divmod(size, 3 + unit)
        if remainder or length < 2:
            continue
        for high in range(11, length - 2, -1):
            core = range(high - length + 1, high + 1)
            if all(counts[item] == 3 for item in core):
                if _wings_match([counts[r] for r in ranks if r not in core], length, unit):
                    return Combination(kind, high, size, length)
    return None


def _wings_match(counts, wings, unit):
    if unit == 2:
        return len(counts) == wings and all(count == 2 for count in counts)
    return sum(counts) == wings and all(count <= 2 for count in counts)


def beats(candidate, previous):
    if candidate is None:
        return False
    if previous is None:
        return True
    if previous.kind == 'rocket':
        return False
    if candidate.kind == 'rocket':
        return True
    if candidate.kind == 'bomb' and previous.kind != 'bomb':
        return True
    return candidate.kind == previous.kind and candidate.size == previous.size and candidate.high > previous.high


def hint(hand, last_cards):
    if not last_cards:
        return [min(hand)] if hand else []
    previous = classify(last_cards)
    if not previous or previous.kind == 'rocket':
        return []
    groups = defaultdict(list)
    for card in sorted(hand):
        groups[rank(card)].append(card)

    # First try the cheapest matching combination; keep bombs for last.
    for kind in (previous.kind, 'bomb', 'rocket'):
        for selection in _matching_cards(groups, kind, previous.length):
            if beats(classify(selection), previous):
                return selection
    return []


def _matching_cards(groups, kind, length):
    if kind == 'rocket':
        if groups[13] and groups[14]:
            yield groups[13] + groups[14]
        return
    copies, wings, unit = {
        'single': (1, 0, 0), 'pair': (2, 0, 0), 'triple': (3, 0, 0), 'bomb': (4, 0, 0),
        'triple_single': (3, 1, 1), 'triple_pair': (3, 1, 2),
        'four_singles': (4, 2, 1), 'four_pairs': (4, 2, 2),
        'straight': (1, 0, 0), 'pairs': (2, 0, 0), 'plane': (3, 0, 0),
        'plane_singles': (3, length, 1), 'plane_pairs': (3, length, 2),
    }[kind]
    if kind == 'bomb':
        length = 1
    for end in range(length - 1, 12 if length > 1 else 15):
        core = range(end - length + 1, end + 1)
        if any(len(groups[item]) < copies for item in core):
            continue
        cards = [card for item in core for card in groups[item][:copies]]
        needed = wings * unit
        if needed:
            side = [card for item in sorted(groups) if item not in core and len(groups[item]) >= unit
                    for card in groups[item][:2]]
            if len(side) < needed:
                continue
            cards.extend(side[:needed])
        yield sorted(cards)
