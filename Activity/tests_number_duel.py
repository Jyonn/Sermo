from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Activity.models import ActivityCampaign, ActivityService, NumberDuel, NumberDuelReward
from Activity.number_duel import DuelErrors, NumberDuelService as Game, feedback, validate_digits
from Space.models import Space
from User.models import User


class NumberDuelTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Duel', slug='duel-test', email='duel@example.test')
        self.host = self.person('Host')
        self.guest = self.person('Guest')
        self.campaign = ActivityCampaign.objects.create(key=Game.KEY, title='Duel', duration_seconds=604800,
                                                       config={'mode': Game.MODE, 'theme': Game.MODE})
        self.run = ActivityService.claim_for_space(self.campaign, self.space)

    def person(self, name):
        user = User.create(self.space, name, email=f'{name}@example.test', verified=True)
        user.email_verified_at = timezone.now()
        user.save(update_fields=['email_verified_at'])
        return user

    def act(self, user=None, **data):
        return Game.act(Game.KEY, user or self.host, data)

    def room(self, guest=None):
        host = self.act(action='create', stake=10)
        self.act(guest or self.guest, action='join', code=host['code'], stake=10)
        return host['id']

    def start(self, guest=None):
        match = self.room(guest)
        self.act(action='secret', id=match, value='0123')
        self.act(guest or self.guest, action='secret', id=match, value='4567')
        return match

    def win(self, guest=None):
        match = self.start(guest)
        first = self.act(action='guess', id=match, round=1, value='4567')
        self.assertEqual(first['status'], 'playing')
        self.act(guest or self.guest, action='guess', id=match, round=1, value='8910')
        return Game.lobby(Game.KEY, self.host)['match'] or Game.serialize(NumberDuel.objects.get(pk=match), self.host)

    def test_validation_feedback_and_leading_zero(self):
        self.assertEqual(validate_digits('0123'), '0123')
        self.assertEqual(feedback('0123', '0324'), (2, 1))
        for invalid in ['1123', '１２３４', 1234, '123', '12345']:
            with self.assertRaises(type(DuelErrors.INVALID)):
                validate_digits(invalid)

    def test_only_explicit_email_or_phone_verification(self):
        self.host.email_verified_at = None
        self.host.save(update_fields=['email_verified_at'])
        with self.assertRaises(type(DuelErrors.ACCOUNT)):
            self.act(action='create')
        self.host.phone_verified_at = timezone.now()
        self.host.save(update_fields=['phone_verified_at'])
        self.assertEqual(self.act(action='create')['status'], 'waiting')

    def test_server_tiers_and_join_agreement(self):
        with self.assertRaises(type(DuelErrors.TIER)):
            self.act(action='create', stake=50)
        match = self.act(action='create')
        preview = self.act(self.guest, action='preview', code=match['code'])
        self.assertEqual(preview['stake'], 10)
        self.assertNotIn('own_secret', preview)
        with self.assertRaises(type(DuelErrors.TIER)):
            self.act(self.guest, action='join', code=match['code'], stake=20)

    def test_tier_boundaries(self):
        for level, expected in [(7, [10]), (8, [10, 20]), (13, [10, 20]), (14, [10, 20, 50])]:
            with patch.object(User, 'calculate_growth', return_value={'level': level}):
                self.assertEqual(Game.tiers(self.host, refresh=True), expected)

    def test_encrypted_secret_private_feedback_and_round_fairness(self):
        match = self.start()
        row = NumberDuel.objects.get(pk=match)
        self.assertNotEqual(row.host_secret, '0123')
        data = self.act(action='guess', id=match, round=1, value='4567')
        self.assertIsNone(data['opponent_secret'])
        other = Game.serialize(row, self.guest)
        self.assertIsNone(other['guesses'][0]['value'])
        result = self.act(self.guest, action='guess', id=match, round=1, value='0123')
        self.assertEqual(result['outcome'], 'draw')
        self.assertEqual(result['opponent_secret'], '0123')
        self.assertEqual(NumberDuelReward.objects.filter(counted=True).count(), 2)

    def test_first_double_loser_zero_and_idempotent_reconciliation(self):
        result = self.win()
        self.assertEqual(result['points'], 20)
        self.assertEqual(NumberDuelReward.objects.get(user=self.guest).points, 0)
        duel = NumberDuel.objects.get(pk=result['id'])
        Game.finish(duel, self.host)
        self.assertEqual(NumberDuelReward.objects.count(), 2)
        self.assertEqual(self.host.growth_events.filter(category='activity').count(), 1)
        self.assertEqual(self.host.reconcile_growth(), self.host.calculate_growth()['score'])
        self.assertTrue(self.host.growth_events.filter(points=20).exists())

    def test_same_opponent_only_first_counts_even_after_loss(self):
        self.win()
        second = self.win()
        self.assertEqual(second['points'], 0)
        self.assertEqual(second['reward_reason'], 'pair')
        self.assertEqual(Game.daily(self.guest, self.run)['matches'], 1)

    def test_loss_consumes_first_slot_without_deduction(self):
        match = self.start()
        self.act(action='guess', id=match, round=1, value='8910')
        self.act(self.guest, action='guess', id=match, round=1, value='0123')
        self.assertEqual(NumberDuelReward.objects.get(user=self.host).points, 0)
        self.assertEqual(self.win(self.person('NewOpponent'))['points'], 10)

    def test_ten_unsolved_rounds_draw(self):
        match = self.start()
        for round_no in range(1, 11):
            self.act(action='guess', id=match, round=round_no, value='8910')
            result = self.act(self.guest, action='guess', id=match, round=round_no, value='8910')
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['outcome'], 'draw')
        self.assertEqual(result['points'], 0)

    def test_custom_activity_and_share_payload_do_not_use_legacy_theme(self):
        self.campaign.config['presentation'] = {'palette': 'sage', 'artwork_url': '/activity/number-duel/digits.svg'}
        self.campaign.save(update_fields=['config'])
        result = ActivityService.payload(self.campaign, self.host)
        self.assertEqual(result['theme'], 'number_duel')
        self.assertEqual(result['presentation']['artwork_url'], '/activity/number-duel/digits.svg')
        self.assertIsNone(result['personal_reward'])

    def test_setup_timeout_and_unchangeable_secret(self):
        match = self.room()
        self.act(action='secret', id=match, value='0123')
        with self.assertRaises(type(DuelErrors.LOCKED)):
            self.act(action='secret', id=match, value='0124')
        NumberDuel.objects.filter(pk=match).update(deadline=timezone.now() - timedelta(seconds=1))
        Game.lobby(Game.KEY, self.host)
        self.assertEqual(NumberDuel.objects.get(pk=match).reason, 'timeout')
        self.assertFalse(NumberDuelReward.objects.filter(counted=True).exists())

    def test_daily_five_match_limit_and_later_single_reward(self):
        rewards = []
        for index in range(6):
            rewards.append(self.win(self.person(f'Peer{index}'))['points'])
        self.assertEqual(rewards, [20, 10, 10, 10, 10, 0])
        self.assertEqual(Game.daily(self.host, self.run)['matches'], 5)

    def test_timeout_skips_one_round_and_disconnect_catches_up(self):
        match = self.start()
        self.act(action='guess', id=match, round=1, value='8910')
        NumberDuel.objects.filter(pk=match).update(deadline=timezone.now() - timedelta(seconds=1))
        data = Game.lobby(Game.KEY, self.host)['match']
        self.assertEqual(data['round'], 2)
        self.assertEqual(data['status'], 'playing')
        NumberDuel.objects.filter(pk=match).update(deadline=timezone.now() - timedelta(seconds=100))
        Game.lobby(Game.KEY, self.host)
        self.assertEqual(NumberDuel.objects.get(pk=match).reason, 'inactive')
        self.assertFalse(NumberDuelReward.objects.filter(counted=True).exists())

    def test_solved_round_waits_then_wins_when_other_times_out(self):
        match = self.start()
        self.act(action='guess', id=match, round=1, value='4567')
        NumberDuel.objects.filter(pk=match).update(deadline=timezone.now() - timedelta(seconds=1))
        Game.lobby(Game.KEY, self.host)
        self.assertEqual(NumberDuelReward.objects.get(user=self.host).points, 20)

    def test_stale_or_repeated_submission_cannot_change_guess(self):
        match = self.start()
        self.act(action='guess', id=match, round=1, value='8910')
        self.act(action='guess', id=match, round=1, value='8910')
        with self.assertRaises(type(DuelErrors.LOCKED)):
            self.act(action='guess', id=match, round=1, value='8912')
        self.assertEqual(NumberDuel.objects.get(pk=match).guesses.count(), 1)

    def test_forfeit_and_activity_end_do_not_award(self):
        match = self.start()
        self.act(action='leave', id=match)
        self.assertFalse(NumberDuelReward.objects.filter(counted=True).exists())
        match = self.start()
        self.run.ends_at = timezone.now() - timedelta(seconds=1)
        self.run.save(update_fields=['ends_at'])
        Game.lobby(Game.KEY, self.host)
        self.assertEqual(NumberDuel.objects.get(pk=match).reason, 'ended')

    def test_cross_space_and_non_participant_denied(self):
        match = self.start()
        stranger = self.person('Stranger')
        with self.assertRaises(type(DuelErrors.FORBIDDEN)):
            Game.lobby(Game.KEY, stranger, match)
        with self.assertRaises(type(DuelErrors.CLOSED)):
            Game.lobby(Game.KEY, self.host, 'not-a-uuid')
        other = Space.objects.create(name='Other', slug='duel-other', email='other@example.test')
        stranger.space = other
        stranger.save(update_fields=['space'])
        with self.assertRaises(type(DuelErrors.CLOSED)):
            Game.lobby(Game.KEY, stranger)

    def test_claim_is_seven_days_and_registration_does_not_start_spaces(self):
        self.assertEqual(self.run.ends_at - self.run.claimed_at, timedelta(days=7))
        self.assertEqual(self.act(action='create')['id'], self.act(action='create')['id'])

    def test_authenticated_http_and_invalid_json(self):
        from utils.auth import get_login_token
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(self.host)['auth']}
        url = f'/activities/{Game.KEY}/duel'
        response = self.client.get(url, **headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['body']['eligible'])
        response = self.client.post(url, data='{"action":"create","stake":10}', content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['body']['status'], 'waiting')
        response = self.client.post(url, data='{', content_type='application/json', **headers)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['identifier'], 'DUEL@INVALID')
        response = self.client.post(url, data='{"action":"guess","id":[]}', content_type='application/json', **headers)
        self.assertEqual(response.status_code, 400)
