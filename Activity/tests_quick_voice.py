from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Activity import four_player
from Activity.landlord import LandlordService
from Activity.models import ActivityCampaign, ActivityService, LandlordTable, NumberDuel
from Activity.number_duel import NumberDuelService
from Activity.quick_voice import QuickVoiceErrors, send_voice
from Space.models import Space
from User.models import User
from utils.auth import get_login_token


class GameQuickVoiceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Room voice', slug='room-voice', email='voice@example.test')
        self.host, self.guest, self.observer = [
            User.create(self.space, name, verified=True, email=f'{name}@example.test')
            for name in ('Host', 'Guest', 'Observer')
        ]
        for user in (self.host, self.guest, self.observer):
            user.email_verified_at = timezone.now()
            user.save(update_fields=['email_verified_at'])
        for key in ('landlord', 'shuangkou', 'mahjong', 'number_duel'):
            campaign, _ = ActivityCampaign.objects.get_or_create(key=key, defaults={
                'title': key, 'config': {'mode': key}, 'duration_seconds': 7 * 86400,
            })
            ActivityService.claim_for_space(campaign, self.space)

    def table(self):
        table = LandlordService.act('landlord', self.host, {'action': 'create'})['table']
        return LandlordService.act('landlord', self.guest, {'action': 'join', 'code': table['code']})['table']

    def test_two_clients_see_the_same_preset_without_mutating_the_round(self):
        table = self.table()
        token = get_login_token(self.host)['auth']
        response = self.client.post('/activities/landlord/quick-voice', data={
            'code': table['code'], 'phrase': 'good_hand',
        }, content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 200, response.content)
        sent = response.json()['body']['messages']
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]['user_id'], self.host.pk)
        self.assertEqual(sent[0]['phrase'], 'good_hand')
        self.assertNotIn('audience', sent[0])
        guest = LandlordService.lobby('landlord', self.guest, table['code'])['table']
        self.assertEqual(guest['quick_voice']['messages'], sent)
        self.assertEqual(guest['version'], table['version'])
        self.assertEqual(guest['deadline'], table['deadline'])
        self.assertIsNone(LandlordService.lobby('landlord', self.observer, table['code'])['table'])

    def test_turn_reminder_is_a_room_preset(self):
        table = self.table()
        original = {'code': table['code'], 'phrase': 'your_turn'}
        sent = send_voice('landlord', self.host, original)
        self.assertEqual(sent['messages'][0]['phrase'], 'your_turn')
        with self.assertRaises(type(QuickVoiceErrors.INVALID)):
            send_voice('landlord', self.host, {**original, 'phrase': 'your_guess'})

    def test_table_and_campaign_membership_presets_and_rate_limits(self):
        table = self.table()
        data = {'code': table['code'], 'phrase': 'wait'}
        for key, user in (('landlord', self.observer), ('mahjong', self.host)):
            with self.assertRaises(type(QuickVoiceErrors.UNAVAILABLE)):
                send_voice(key, user, data)
        for phrase in ('your_call', 'arbitrary text', ['wait']):
            with self.assertRaises(type(QuickVoiceErrors.INVALID)):
                send_voice('landlord', self.host, {**data, 'phrase': phrase})
        stamp = timezone.now()
        send_voice('landlord', self.host, data)
        with self.assertRaises(type(QuickVoiceErrors.COOLDOWN)):
            send_voice('landlord', self.host, data)
        send_voice('landlord', self.guest, data)
        with patch('Activity.quick_voice.timezone.now', return_value=stamp + timedelta(seconds=9)):
            self.assertEqual(LandlordService.lobby('landlord', self.guest, table['code'])['table']['quick_voice']['messages'], [])
            send_voice('landlord', self.host, data)
        room = LandlordTable.objects.get(code=table['code'])
        self.assertEqual([event['id'] for event in room.quick_voice_events], [3])

    def test_new_seat_does_not_receive_old_room_voice(self):
        table = self.table()
        data = {'code': table['code'], 'phrase': 'hello'}
        send_voice('landlord', self.host, data)
        joined = LandlordService.act('landlord', self.observer, {'action': 'join', 'code': table['code']})['table']
        self.assertTrue(joined['quick_voice']['enabled'])
        self.assertEqual(joined['quick_voice']['messages'], [])

    def test_both_four_player_games_and_duel_share_the_same_payload(self):
        for key in ('shuangkou', 'mahjong'):
            with self.subTest(key=key):
                room = four_player.act(key, self.host, {'action': 'create'})['table']
                self.assertFalse(room['quick_voice']['enabled'])
                four_player.act(key, self.guest, {'action': 'join', 'code': room['code']})
                sent = send_voice(key, self.host, {'code': room['code'], 'phrase': 'good_hand'})
                self.assertEqual(four_player.lobby(key, self.guest, room['code'])['table']['quick_voice']['messages'], sent['messages'])
        game = NumberDuelService
        duel = game.act('number_duel', self.host, {'action': 'create', 'room_flow': 2})
        game.act('number_duel', self.guest, {'action': 'join', 'code': duel['code'], 'room_flow': 2})
        sent = send_voice('number_duel', self.guest, {'code': duel['code'], 'phrase': 'your_call'})
        self.assertEqual(game.lobby('number_duel', self.host, room_code=duel['code'])['match']['quick_voice']['messages'], sent['messages'])
        NumberDuel.objects.filter(code=duel['code']).update(status='finished', reason='forfeit', finished_at=timezone.now())
        with self.assertRaises(type(QuickVoiceErrors.UNAVAILABLE)):
            send_voice('number_duel', self.host, {'code': duel['code'], 'phrase': 'hello'})
