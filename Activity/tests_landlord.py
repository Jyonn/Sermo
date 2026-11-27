import json
from datetime import datetime, timedelta, timezone as utc
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.test.client import RequestFactory
from django.utils import timezone

from Activity.landlord import LandlordService
from Activity.card_game import card_rank, deal
from Activity.landlord_rules import beats, classify, hint
from Activity.models import ActivityCampaign, ActivityService, LandlordSeat, LandlordTable, SpaceActivity
from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Message.validators import MessageErrors
from Space.models import Space
from User.models import User
from utils import auth


def cards(*ranks):
    result = []
    for rank in ranks:
        if rank >= 13:
            result.append(rank + 39)
        else:
            choices = range(rank * 4, rank * 4 + 4)
            result.append(next(card for card in choices if card not in result))
    return result


class LandlordRulesTests(SimpleTestCase):
    def test_deck_supports_four_players_using_two_decks(self):
        hands, bottom = deal(players=4, cards_per_player=27, decks=2)
        self.assertEqual([len(hand) for hand in hands], [27] * 4)
        self.assertFalse(bottom)
        self.assertEqual(set().union(*map(set, hands)), set(range(108)))
        self.assertEqual(card_rank(53), card_rank(107))

    def test_classic_patterns_and_comparison(self):
        examples = {
            'pair': (0, 0), 'triple': (1, 1, 1), 'triple_single': (0, 0, 0, 10),
            'triple_pair': (4, 4, 4, 1, 1), 'straight': (0, 1, 2, 3, 4),
            'pairs': (0, 0, 1, 1, 2, 2), 'plane': (0, 0, 0, 1, 1, 1),
            'plane_singles': (0, 0, 0, 1, 1, 1, 7, 7), 'plane_pairs': (0, 0, 0, 1, 1, 1, 7, 7, 9, 9),
            'four_singles': (4, 4, 4, 4, 0, 1), 'four_pairs': (4, 4, 4, 4, 0, 0, 1, 1),
            'bomb': (1, 1, 1, 1), 'rocket': (13, 14),
        }
        for kind, example in examples.items():
            with self.subTest(kind=kind):
                self.assertEqual(classify(cards(*example)).kind, kind)
        self.assertIsNone(classify(cards(8, 9, 10, 11, 12)))
        self.assertIsNone(classify([4, 4]))
        self.assertIsNone(classify([False]))
        self.assertIsNone(classify(cards(0, 0, 0, 1, 1, 1, 7, 7, 7, 8)))
        self.assertFalse(beats(classify(cards(1, 1)), classify(cards(0, 0, 0))))
        self.assertFalse(beats(classify(cards(1, 2, 3, 4, 5, 6)), classify(cards(0, 1, 2, 3, 4))))
        self.assertTrue(beats(classify(cards(1, 1, 1, 1)), classify(cards(13))))
        self.assertTrue(beats(classify(cards(13, 14)), classify(cards(12, 12, 12, 12))))

    def test_hint_prefers_matching_plays_and_never_invents_cards(self):
        hand = cards(1, 1, 1, 2, 2, 2, 5, 5, 6, 6, 7, 7, 7, 7, 13, 14)
        previous = cards(0, 0, 0, 1, 1, 1, 3, 3, 4, 4)
        chosen = hint(hand, previous)
        self.assertEqual(classify(chosen).kind, 'plane_pairs')
        self.assertTrue(beats(classify(chosen), classify(previous)))
        self.assertTrue(set(chosen).issubset(hand))
        self.assertEqual(hint(hand, cards(13, 14)), [])


class LandlordActivityTests(TestCase):
    endpoint = '/activities/landlord/landlord'

    def setUp(self):
        self.space = Space.objects.create(name='Club', slug='landlord-test', email='host@example.test')
        self.users = [User.create(self.space, name, verified=True) for name in ('Alice', 'Bo', 'Chen')]
        self.campaign = ActivityCampaign.objects.get(key=LandlordService.KEY)

    @staticmethod
    def headers(user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user)['auth']}

    def start_space(self):
        return ActivityService.claim_for_space(self.campaign, self.space)

    def send_action(self, user, status=200, **data):
        response = self.client.post(self.endpoint, data, content_type='application/json', **self.headers(user))
        self.assertEqual(response.status_code, status, response.content)
        return response.json()['body']

    def snapshot(self, user, code=None):
        response = self.client.get(self.endpoint, {'code': code} if code else {}, **self.headers(user))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn('no-store', response['Cache-Control'])
        return response.json()['body']

    def table(self):
        first, second, third = self.users
        table = self.send_action(first, action='create')['table']
        self.assertEqual(self.send_action(first, action='create')['table']['code'], table['code'])
        self.send_action(second, action='join', code=table['code'])
        with patch('Activity.landlord.secrets.randbelow', return_value=0):
            result = self.send_action(third, action='join', code=table['code'])
        return result['table']['code']

    def test_admin_claim_is_manual_permanent_and_only_verified_users_can_play(self):
        member = self.users[0]
        basic = User.create(self.space, 'Unauthenticated member')
        self.assertEqual(self.send_action(member, 403, action='create'), None)
        self.assertFalse(SpaceActivity.objects.filter(campaign=self.campaign, space=self.space).exists())
        url = '/activities/admin/landlord/claim'
        self.assertGreaterEqual(self.client.post(url, **self.headers(member)).status_code, 400)
        admin_headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_space_login_token(self.space)['auth']}
        result = self.client.post(url, **admin_headers)
        self.assertEqual(result.status_code, 200, result.content)
        row = next(item for item in result.json()['body'] if item['key'] == 'landlord')
        self.assertEqual(row['status'], 'active')
        self.assertIsNone(row['ends_at'])
        self.assertEqual(row['assignment_mode'], 'manual')
        self.send_action(basic, 403, action='create')
        self.assertEqual(self.client.get(self.endpoint + '/leaderboard', **self.headers(basic)).status_code, 200)
        details = self.client.get('/activities/landlord', **self.headers(member)).json()['body']
        self.assertTrue(details['verified'])

    def test_deal_resume_validate_turns_finish_and_rank(self):
        self.start_space()
        code = self.table()
        members = self.users
        initial = [self.snapshot(user, code)['table'] for user in members]
        self.assertEqual([len(table['hand']) for table in initial], [17, 17, 17])
        self.assertEqual(len(set(card for table in initial for card in table['hand'])), 51)
        self.assertTrue(all(not table['bottom_cards'] for table in initial))
        self.assertTrue(all('hand' not in seat for seat in initial[0]['seats']))
        self.assertIsNone(self.snapshot(User.create(self.space, 'Spectator', verified=True), code)['table'])
        self.send_action(members[1], 400, action='bid', code=code, version=initial[1]['version'], value=3)
        current = self.send_action(members[0], action='bid', code=code, version=initial[0]['version'], value=3)['table']
        self.assertEqual(len(current['hand']), 20)
        self.assertEqual(len(current['bottom_cards']), 3)
        self.assertEqual(current['status'], 'doubling')
        stale_version = current['version']
        for index, value in enumerate([True, True, False]):
            current = self.send_action(members[index], action='double', code=code, version=stale_version, value=value)['table']
        self.assertEqual(current['status'], 'playing')
        self.assertEqual([seat['doubled'] for seat in current['seats']], [True, True, False])
        current = self.snapshot(members[0], code)['table']
        self.assertEqual(self.snapshot(members[0])['table']['code'], code)
        foreign = initial[1]['hand'][0]
        self.send_action(members[0], 400, action='play', code=code, version=current['version'], cards=[foreign])
        self.send_action(members[0], 400, action='pass', code=code, version=current['version'])
        first = current['hand'][0]
        play = dict(action='play', code=code, version=current['version'], cards=[first])
        current = self.send_action(members[0], **play)['table']
        self.assertTrue(self.send_action(members[0], **play)['changed'])
        self.assertEqual(len(current['hand']), 19)
        for _ in range(60):
            if current['status'] == 'finished':
                break
            turn = current['turn']
            move = dict(code=code, version=current['version'])
            if turn == 0:
                own = self.snapshot(members[0], code)['table']
                current = self.send_action(members[0], action='play', cards=[own['hand'][0]], **move)['table']
            else:
                current = self.send_action(members[turn], action='pass', **move)['table']
        self.assertEqual((current['status'], current['winner']), ('finished', 0))
        self.assertTrue(current['spring'])
        self.assertEqual([seat['score'] for seat in current['seats']], [36, -24, -12])
        rank = self.client.get(self.endpoint + '/leaderboard', **self.headers(members[0])).json()['body']
        self.assertEqual(rank['mine'], {'rank': 1, 'wins': 1, 'games': 1, 'points': 36, 'win_rate': 1.0})
        self.assertEqual([row['rank'] for row in rank['rows']], [1, 2, 3])
        self.assertEqual([row['wins'] for row in rank['rows']], [1, 0, 0])
        ranked_by_rate = self.client.get(self.endpoint + '/leaderboard', {'metric': 'win_rate'}, **self.headers(members[0])).json()['body']
        self.assertEqual([row['rank'] for row in ranked_by_rate['rows']], [1, 2, 2])

    def test_group_invitation_has_a_room_reference_for_group_members(self):
        self.start_space()
        author, guest, outsider = self.users
        chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=author)
        for user in self.users:
            ChatMember.objects.create(chat=chat, user=user, role=ChatMemberRoleChoice.MEMBER,
                                      status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now())
        waiting = self.send_action(author, action='create')['table']
        reference = json.dumps({'kind': 'landlord_room', 'activity_key': 'landlord', 'room_code': waiting['code']})
        message = Message.create(chat, author, MessageTypeChoice.ACTIVITY, reference)
        request = RequestFactory().get('/')
        request.user = guest
        payload = message._payload_for_type(request)
        self.assertEqual(payload['landlord_room_code'], waiting['code'])
        self.send_action(guest, action='join', code=payload['landlord_room_code'])
        with self.assertRaises(MessageErrors.PAYLOAD_INVALID.__class__):
            Message.create(chat, outsider, MessageTypeChoice.ACTIVITY, reference)

    def test_doubling_timeout_and_manually_toggled_trustee(self):
        self.start_space()
        code = self.table()
        row = LandlordTable.objects.get(code=code)
        self.send_action(self.users[0], action='bid', code=code, version=row.version, value=3)
        LandlordTable.objects.filter(pk=row.id).update(deadline=timezone.now() - timedelta(seconds=1))
        current = self.snapshot(self.users[0], code)['table']
        self.assertEqual(current['status'], 'playing')
        self.assertEqual([seat['doubled'] for seat in current['seats']], [False] * 3)
        current = self.send_action(self.users[0], action='trustee', code=code, version=current['version'], value=True)['table']
        self.assertTrue(current['seats'][0]['trustee'])
        LandlordTable.objects.filter(pk=row.id).update(deadline=timezone.now() - timedelta(seconds=1))
        current = self.snapshot(self.users[0], code)['table']
        self.assertTrue(current['moves'][0]['automatic'])
        self.assertGreater(len(current['moves'][0]['cards']), 0)
        current = self.send_action(self.users[0], action='trustee', code=code, version=current['version'], value=False)['table']
        self.assertFalse(current['seats'][0]['trustee'])
        self.assertEqual(current['turn'], 1)

    def test_expired_turn_resolves_on_read_and_abandonment_does_not_rank(self):
        self.start_space()
        code = self.table()
        row = LandlordTable.objects.get(code=code)
        old_version = row.version
        LandlordTable.objects.filter(pk=row.id).update(deadline=timezone.now() - timedelta(seconds=1))
        current = self.snapshot(self.users[1], code)['table']
        self.assertEqual(current['bids'], [0, None, None])
        self.assertEqual(current['turn'], 1)
        self.assertTrue(current['seats'][0]['trustee'])
        self.assertGreater(current['version'], old_version)
        self.send_action(self.users[1], action='leave', code=code, version=current['version'])
        self.assertEqual(self.snapshot(self.users[0], code)['table']['status'], 'abandoned')
        self.assertEqual(LandlordService.leaderboard('landlord', self.users[0], None)['rows'], [])

    def test_ranking_uses_beijing_year_and_excludes_other_spaces(self):
        run = self.start_space()
        run.claimed_at = datetime(2025, 1, 1, tzinfo=utc.utc)
        run.save(update_fields=['claimed_at'])
        for code, utc_hour in [('ABCDEFGH', 15), ('JKMNPQRS', 16)]:
            row = LandlordTable.objects.create(run=run, code=code, status='finished',
                                               finished_at=datetime(2025, 12, 31, utc_hour, 59, tzinfo=utc.utc))
            LandlordSeat.objects.create(table=row, user=self.users[0], position=0, won=True)
        other = Space.objects.create(name='Other Club', slug='landlord-other', email='other@example.test')
        ActivityService.claim_for_space(self.campaign, other)
        outsider = User.create(other, 'Outsider', verified=True)
        self.send_action(outsider, 400, action='join', code='ABCDEFGH')
        with patch('Activity.landlord.timezone.now', return_value=datetime(2026, 1, 2, tzinfo=utc.utc)):
            self.assertEqual(LandlordService.leaderboard('landlord', self.users[0], '2025')['mine']['wins'], 1)
            self.assertEqual(LandlordService.leaderboard('landlord', self.users[0], '2026')['mine']['wins'], 1)

    def test_points_and_win_rate_use_different_primary_metrics(self):
        run = self.start_space()
        for index, scores in enumerate(((2, -1, -1), (2, -1, -1), (-8, 4, 4))):
            table = LandlordTable.objects.create(run=run, code=f'ABCDEFG{index + 2}', status='finished', finished_at=timezone.now())
            LandlordSeat.objects.bulk_create([
                LandlordSeat(table=table, user=user, position=position, won=score > 0, score=score)
                for position, (user, score) in enumerate(zip(self.users, scores))
            ])
        points = LandlordService.leaderboard('landlord', self.users[0], None)
        rate = LandlordService.leaderboard('landlord', self.users[0], None, 'win_rate')
        self.assertEqual(points['mine']['rank'], 3)
        self.assertEqual(rate['mine']['rank'], 1)
        self.assertEqual((points['rows'][0]['points'], rate['rows'][0]['wins']), (2, 2))
