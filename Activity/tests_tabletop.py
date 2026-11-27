import json

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from Activity import mahjong, shuangkou
from Activity.models import ActivityCampaign, FourPlayerResult, FourPlayerTable, SpaceActivity
from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth


class TabletopRulesTests(SimpleTestCase):
    def test_firefight_wildcards_and_star_comparison(self):
        five = [*range(4), 53]
        self.assertIsNone(shuangkou.classify(five, 'classic'))
        self.assertEqual(shuangkou.classify(five, 'firefight').stars, 5)
        chain = [card for rank in range(3) for card in range(rank * 4, rank * 4 + 4)]
        ring = shuangkou.classify(chain, 'firefight')
        three = shuangkou.classify([52, 53, 106], 'firefight')
        royal = shuangkou.classify([52, 53, 106, 107], 'firefight')
        self.assertEqual((three.stars, ring.stars, royal.stars), (6, 7, 7))
        self.assertTrue(shuangkou.beats(royal, ring))
        self.assertTrue(shuangkou.beats(ring, three))
        self.assertEqual(shuangkou.hint(five, None, 'firefight'), five)

    def test_shuangkou_partner_leads_after_a_retired_player(self):
        board = shuangkou.start('firefight', 0)
        self.assertEqual(len({card for hand in board['hands'] for card in hand}), 108)
        partner = (board['turn'] + 2) % 4
        self.assertIn((board['opening'] + 54) % 108, board['hands'][partner])
        board['hands'] = [[0], [4], [8], [12]]
        board['turn'] = 0
        shuangkou.play(board, 0, [0], 'firefight')
        for seat in (1, 2, 3):
            shuangkou.play(board, seat, [], 'firefight')
        self.assertEqual((board['turn'], board['last']), (2, []))
        shuangkou.play(board, 2, [8], 'firefight')
        self.assertEqual(board['outcome'], 'double')
        self.assertEqual(board['scores'], [4, -4, 4, -4])

    def test_mahjong_shapes_and_priority(self):
        self.assertTrue(mahjong.winning([rank * 4 + copy for rank in range(7) for copy in range(2)]))
        self.assertTrue(mahjong.winning([rank * 4 for rank in mahjong.ORPHANS] + [1]))
        board = mahjong.start('standard', 0)
        board['hands'] = [[0], [4, 8], [1, 2], [12, 16, 20, 36, 40, 44, 72, 76, 80, 108, 109, 110, 3]]
        mahjong.discard(board, 0, 0)
        pending = board['pending']
        self.assertEqual([pending['offers'][str(seat)][0]['kind'] for seat in (1, 2, 3)], ['chi', 'pong', 'hu'])
        for seat in (1, 2, 3):
            mahjong.vote(board, seat, 0, board['decision'])
        self.assertEqual(board['outcome'], 'discard_win')
        self.assertEqual(board['scores'], [-3, 0, 0, 3])

    def test_classic_bomb_does_not_multiply_the_round_score(self):
        board = shuangkou.start('classic', 0)
        board.update(hands=[[0, 1, 2, 3, 54], [4], [8], [12]], turn=0)
        shuangkou.play(board, 0, board['hands'][0], 'classic')
        for seat in (1, 2, 3):
            shuangkou.play(board, seat, [], 'classic')
        shuangkou.play(board, 2, [8], 'classic')
        self.assertEqual(board['scores'], [3, -3, 3, -3])


class TabletopRoomTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Tabletop', slug='tabletop', email='tabletop@example.test')
        self.players = [User.create(self.space, name, verified=True) for name in ('East', 'South', 'West', 'North')]
        self.basic = User.create(self.space, 'Visitor')

    def header(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user)['auth']}

    def act(self, user, game, action, state=None, expected=200, **fields):
        if state:
            fields = dict(code=state['code'], version=state['version'], round=state['round'], **fields)
        reply = self.client.post(f'/activities/{game}/table', dict(action=action, **fields),
                                 content_type='application/json', **self.header(user))
        self.assertEqual(reply.status_code, expected, reply.content)
        return reply.json()['body']

    def claim(self, game):
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_space_login_token(self.space)['auth']}
        reply = self.client.post(f'/activities/admin/{game}/claim', **headers)
        self.assertEqual(reply.status_code, 200, reply.content)
        campaign = ActivityCampaign.objects.get(key=game)
        self.assertEqual(campaign.assignment_mode, 'manual')
        self.assertIsNone(campaign.duration_seconds)

    def setup_room(self, game, mode=None):
        self.claim(game)
        self.act(self.basic, game, 'create', expected=403)
        state = self.act(self.players[0], game, 'create')['table']
        for user in self.players[1:]:
            state = self.act(user, game, 'join', code=state['code'])['table']
        if mode:
            self.act(self.players[1], game, 'mode', state, expected=400, value=mode)
            state = self.act(self.players[0], game, 'mode', state, value=mode)['table']
        for user in self.players:
            state = self.act(user, game, 'ready', state, value=True)['table']
        return self.act(self.players[0], game, 'start', state)['table']

    def test_firefight_room_masks_cards_and_persists_game_results(self):
        self.players[2] = self.space.ensure_official_user()
        self.act(self.players[0], 'shuangkou', 'create', expected=403)
        self.assertFalse(SpaceActivity.objects.filter(space=self.space).exists())
        state = self.setup_room('shuangkou', 'firefight')
        self.assertEqual(state['mode'], 'firefight')
        self.assertEqual(len(state['board']['hand']), 27)
        self.assertNotIn('hands', state['board'])
        table = FourPlayerTable.objects.get(code=state['code'])
        table.board['hands'] = [[0], [4], [8], [12]]
        table.board['turn'] = 0
        table.save(update_fields=['board'])
        self.act(self.players[1], 'shuangkou', 'play', state, expected=400, cards=[4])
        state = self.act(self.players[0], 'shuangkou', 'play', state, cards=[0])['table']
        for index in (1, 2, 3):
            state = self.act(self.players[index], 'shuangkou', 'pass', state)['table']
        state = self.act(self.players[2], 'shuangkou', 'play', state, cards=[8])['table']
        self.assertEqual(state['status'], 'finished')
        self.assertEqual(FourPlayerResult.objects.filter(table=table, mode='firefight').count(), 4)
        state = self.act(self.players[0], 'shuangkou', 'ready', state, value=True)['table']
        state = self.act(self.players[0], 'shuangkou', 'mode', state, value='classic')['table']
        self.assertFalse(state['seats'][0]['ready'])

    def test_mahjong_keeps_the_wall_private_and_invites_keep_the_room(self):
        self.claim('mahjong')
        host = self.players[0] = self.space.ensure_official_user()
        details = self.client.get('/activities/mahjong', **self.header(host)).json()['body']
        self.assertTrue(details['verified'])
        state = self.act(host, 'mahjong', 'create')['table']
        group = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=host)
        for user in self.players:
            ChatMember.objects.create(chat=group, user=user, role=ChatMemberRoleChoice.MEMBER,
                                      status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now())
        message = Message.create(group, host, MessageTypeChoice.ACTIVITY, json.dumps({
            'kind': 'table_room', 'activity_key': 'mahjong', 'room_code': state['code'],
        }))
        reader = RequestFactory().get('/')
        reader.user = self.players[1]
        self.assertEqual(message._payload_for_type(reader)['table_room_code'], state['code'])
        for user in self.players[1:]:
            state = self.act(user, 'mahjong', 'join', code=state['code'])['table']
        for user in self.players:
            state = self.act(user, 'mahjong', 'ready', state, value=True)['table']
        state = self.act(host, 'mahjong', 'start', state)['table']
        self.assertEqual(len(state['board']['hand']), 14)
        self.assertEqual(state['board']['wall_count'], 83)
        self.assertNotIn('wall', state['board'])
        self.assertNotIn('hands', state['board'])
        self.act(host, 'mahjong', 'discard', state, tile=state['board']['hand'][0])

    def test_forfeit_charges_the_quitter_and_leaves_the_partner_neutral(self):
        for game, scores in (('shuangkou', {0: -2, 1: 1, 3: 1}), ('mahjong', {0: -3, 1: 1, 2: 1, 3: 1})):
            with self.subTest(game=game):
                state = self.setup_room(game)
                self.assertIsNone(self.act(self.players[0], game, 'leave', state)['table'])
                table = FourPlayerTable.objects.get(code=state['code'])
                self.assertEqual((table.status, table.host_position), ('finished', 1))
                self.assertEqual(table.seats.count(), 3)
                self.assertEqual(table.board['outcome'], 'forfeit')
                self.assertEqual(dict(table.results.values_list('user_id','score')),
                                 {self.players[position].pk: score for position, score in scores.items()})
