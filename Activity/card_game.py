"""Shared primitives for shedding games using one or more French-suited decks."""
from collections import defaultdict
from datetime import timedelta
from secrets import SystemRandom


DECK_SIZE = 54
FORCED_PASS_TIME = timedelta(seconds=5)


def card_rank(card):
    face = card % DECK_SIZE
    return face // 4 if face < 52 else face - 39


def valid_cards(cards, *, decks=1, maximum):
    return (0 < len(cards) <= maximum
            and all(type(card) is int and 0 <= card < decks * DECK_SIZE for card in cards)
            and len(set(cards)) == len(cards))


def group_by_rank(hand):
    groups = defaultdict(list)
    for card in sorted(hand, key=card_rank):
        groups[card_rank(card)].append(card)
    return groups


def deal(*, players, cards_per_player, decks=1):
    deck = list(range(DECK_SIZE * decks))
    SystemRandom().shuffle(deck)
    return distribute(deck, players, cards_per_player)


def distribute(deck, players, cards_per_player):
    if players * cards_per_player > len(deck):
        raise ValueError('Not enough cards for the deal')
    hands = [sorted(deck[start:start + cards_per_player], key=card_rank)
             for start in range(0, players * cards_per_player, cards_per_player)]
    return hands, sorted(deck[players * cards_per_player:], key=card_rank)


def consecutive(ranks, minimum, *, final_rank=11):
    return len(ranks) >= minimum and ranks[-1] <= final_rank and ranks[-1] - ranks[0] + 1 == len(ranks)


def next_position(position, players):
    return (position + 1) % players


def advance_trick(position, leader, players):
    following = next_position(position, players)
    return following, following == leader


def automatic_play(hand, previous, recommend, *, teammate_leads=False, save=None):
    cards = recommend(hand, previous)
    if previous and len(cards) != len(hand) and (teammate_leads or (save and save(cards))):
        return []
    return cards
