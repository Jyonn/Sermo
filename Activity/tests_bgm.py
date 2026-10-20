from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Activity.bgm import BgmErrors, BgmService
from Activity.models import ActivityCampaign, ActivityService, BgmInteraction, BgmParticipation, UserActivityProgress
from Message.models import MediaAsset
from Space.models import Space
from Square.models import Statement, StatementComment
from User.models import User


class BgmTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='BGM', slug='bgm-test', email='bgm@example.test')
        self.user = User.create(self.space, 'Collector', email='a@example.test', verified=True)
        self.peer = User.create(self.space, 'Peer', email='b@example.test', verified=True)
        tracks = []
        for index in range(12):
            asset = MediaAsset.objects.create(source_key=f'activity/bgm/{index}.mp3', source_uri=f'https://example.test/{index}.mp3', kind=2, status=1, duration_seconds=12, raw_metadata={'bgm_public': True})
            tracks.append(dict(asset_id=asset.pk, title=f'Track {index}', author=''))
        self.campaign = ActivityCampaign.objects.create(key='bgm-test', title='BGM', duration_seconds=30 * 86400, config={'mode': 'bgm_collection', 'theme': 'bgm', 'tracks': tracks})
        self.run = ActivityService.claim_for_space(self.campaign, self.space)
        self.join()
        self.progress = UserActivityProgress.objects.get(user=self.user, campaign=self.campaign)

    def join(self):
        return BgmService.configure(self.campaign, self.user, {'style': 'blue'})

    def statement(self, user=None, **kwargs):
        return Statement.objects.create(space=self.space, user=user or self.peer, text='A real post', **kwargs)

    def chat(self, member):
        from Chat.models import Chat, ChatMember
        chat = Chat.objects.create(space=self.space, chat_type=1, created_by=member, title='BGM group')
        ChatMember.objects.create(chat=chat, user=member, role=1, status=1)
        return chat

    def test_join_gift_and_server_validated_slots(self):
        data = self.join()['bgm']
        self.assertEqual([t['key'] for t in data['tracks'] if t['owned']], ['1'])
        self.assertIsNone(data['tracks'][1]['uri'])
        with self.assertRaises(type(BgmErrors.INVALID)):
            BgmService.configure(self.campaign, self.user, {'style': 'blue', 'slots': ['12']})
        with self.assertRaises(type(BgmErrors.STYLE_LOCKED)):
            BgmService.configure(self.campaign, self.user, {'style': 'pink', 'slots': ['1']})
        data = BgmService.configure(self.campaign, self.user, {'style': 'blue', 'slots': ['1']})['bgm']
        self.assertEqual(data['style'], 'blue')
        self.assertEqual(data['slots'], ['1'])

    def test_peer_audio_is_enabled_even_for_legacy_opt_out(self):
        data = BgmService.configure(self.campaign, self.user, {'style': 'blue', 'receive_audio': False})['bgm']
        self.assertTrue(data['receive_audio'])
        self.assertTrue(BgmService.payload(self.campaign, self.user, self.run)['bgm']['receive_audio'])

    def test_style_lock_preserves_preferences_and_allows_same_style_updates(self):
        self.join()
        with self.assertRaises(type(BgmErrors.STYLE_LOCKED)):
            BgmService.configure(self.campaign, self.user, {'style': 'pink', 'mode': 'auto'})
        participation = BgmParticipation.objects.get(progress=self.progress)
        self.assertEqual(participation.style, 'blue')
        self.assertEqual(participation.mode, 'off')
        result = BgmService.configure(self.campaign, self.user, {'style': 'blue', 'mode': 'auto', 'track': 'random'})
        self.assertEqual(result['bgm']['mode'], 'auto')
        self.assertEqual(result['bgm']['style'], 'blue')

    def test_new_user_can_choose_pink_once(self):
        result = BgmService.configure(self.campaign, self.peer, {'style': 'pink'})
        self.assertEqual(result['bgm']['style'], 'pink')
        with self.assertRaises(type(BgmErrors.STYLE_LOCKED)):
            BgmService.configure(self.campaign, self.peer, {'style': 'blue'})

    def test_daily_cap_distinct_targets_and_relike(self):
        post = self.statement(self.user)
        for _ in range(3): BgmService.record(self.user, 'publish', post)
        for index in range(6):
            peer = User.create(self.space, f'P{index}', email=f'p{index}@example.test', verified=True)
            target = self.statement(peer)
            BgmService.record(self.user, 'like', target)
            BgmService.record(self.user, 'like', target)
            comment = StatementComment.objects.create(statement=target, user=self.user, text='A thoughtful response')
            BgmService.record(self.user, 'comment', comment)
            repeat = StatementComment.objects.create(statement=target, user=self.user, text='Another response')
            BgmService.record(self.user, 'comment', repeat)
        self.assertEqual(BgmService.stats(self.progress)[:2], (20, 1))
        self.assertEqual(BgmService.payload(self.campaign, self.user, self.run)['bgm']['today_points'], 20)

    def test_likes_alone_do_not_make_an_active_day(self):
        BgmService.record(self.user, 'like', self.statement())
        self.assertEqual(BgmService.stats(self.progress)[:2], (1, 0))
        for _ in range(2):
            comment = StatementComment.objects.create(statement=self.statement(), user=self.user, text='Hello')
            BgmService.record(self.user, 'comment', comment)
        self.assertEqual(BgmService.stats(self.progress)[:2], (7, 1))

    def test_public_self_anonymous_unverified_and_other_space_rules(self):
        for target in [self.statement(self.user), self.statement(visibility=1), self.statement(is_anonymous=True)]:
            BgmService.record(self.user, 'like', target)
        other = Space.objects.create(name='Other', slug='bgm-other', email='other@example.test')
        target = Statement.objects.create(space=other, user=self.peer, text='Other space')
        BgmService.record(self.user, 'like', target)
        self.user.account_level = 0
        BgmService.record(self.user, 'publish', self.statement(self.user))
        self.assertEqual(BgmInteraction.objects.count(), 0)

    def test_at_least_fifteen_distinct_active_days_even_with_unlimited_points(self):
        today = timezone.localdate(timezone=BgmService.ZONE)
        for day in range(14):
            BgmInteraction.objects.create(progress=self.progress, kind='publish', reference=str(day), target=str(day), event_date=today - timedelta(days=day), points=20)
        tracks = BgmService.payload(self.campaign, self.user, self.run)['bgm']['tracks']
        self.assertFalse(tracks[-1]['owned'])
        BgmInteraction.objects.create(progress=self.progress, kind='publish', reference='15', target='15', event_date=today - timedelta(days=18), points=20)
        self.assertTrue(BgmService.payload(self.campaign, self.user, self.run)['bgm']['tracks'][-1]['owned'])

    def test_expiry_freezes_points_but_preserves_collection(self):
        self.run.ends_at = timezone.now() - timedelta(seconds=1)
        self.run.save(update_fields=['ends_at'])
        BgmService.record(self.user, 'publish', self.statement(self.user))
        self.assertEqual(BgmInteraction.objects.count(), 0)
        result = BgmService.configure(self.campaign, self.user, {'style': 'blue'})
        self.assertFalse(result['active'])
        self.assertTrue(result['bgm']['tracks'][0]['owned'])

    def test_task_day_uses_beijing_time_and_no_prejoin_history(self):
        BgmParticipation.objects.all().delete()
        BgmService.record(self.user, 'publish', self.statement(self.user))
        self.assertEqual(BgmInteraction.objects.count(), 0)
        self.join()
        with patch('Activity.bgm.timezone.localdate', return_value=timezone.datetime(2026, 10, 21).date()):
            BgmService.record(self.user, 'publish', self.statement(self.user))
        self.assertEqual(BgmInteraction.objects.get().event_date.isoformat(), '2026-10-21')

    def test_entrance_creates_legacy_audio_and_system_pair_with_cooldown(self):
        from Chat.models import Chat
        from Message.models import Message, MessageTypeChoice
        chat = self.chat(self.user)
        BgmService.configure(self.campaign, self.user, {'style': 'blue', 'mode': 'manual'})
        response = BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0001', 'track': '1'})
        self.assertTrue(response['sent'])
        audio = Message.objects.get(pk=response['audio_message_id'])
        self.assertEqual(audio.type, MessageTypeChoice.AUDIO)
        self.assertIsNotNone(audio.media_resource_id)
        payload = audio._payload_for_type()
        self.assertTrue(payload['bgm_entrance'])
        system = Message.objects.get(pk=payload['system_message_id'])
        self.assertEqual(system.type, MessageTypeChoice.SYSTEM)
        self.assertIn('Track 0', system.system_message_text(self.user))
        self.assertEqual(BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0001', 'track': '1'})['reason'], 'duplicate')
        self.assertEqual(BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0002', 'track': '1'})['reason'], 'cooldown')
        self.assertEqual(BgmInteraction.objects.count(), 0)

    def test_entry_requires_membership_and_owned_equipped_track(self):
        from Chat.models import Chat
        chat = self.chat(self.peer)
        with self.assertRaises(type(BgmErrors.CLOSED)):
            BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0001', 'track': '1'})
        own_chat = self.chat(self.user)
        with self.assertRaises(type(BgmErrors.CLOSED)):
            BgmService.enter(self.campaign, self.user, {'chat_id': own_chat.id, 'request_id': 'request-0001'})
        BgmService.configure(self.campaign, self.user, {'style': 'blue', 'mode': 'manual'})
        with self.assertRaises(type(BgmErrors.INVALID)):
            BgmService.enter(self.campaign, self.user, {'chat_id': own_chat.id, 'request_id': 'request-0001', 'track': '12'})

    def test_authenticated_settings_endpoint_and_no_client_awarded_points(self):
        from utils import auth
        authorization = {'HTTP_AUTHORIZATION': f'Bearer {auth.get_login_token(self.user)["auth"]}'}
        response = self.client.post('/activities/bgm-test/bgm', data={'style': 'blue', 'slots': ['1']}, content_type='application/json', **authorization)
        self.assertEqual(response.json()['body']['bgm']['style'], 'blue')
        locked = self.client.post('/activities/bgm-test/bgm', data={'style': 'pink'}, content_type='application/json', **authorization)
        self.assertNotEqual(locked.json()['identifier'], 'OK')
        forged = self.client.post('/activities/bgm-test/bgm', data={'style': 'blue', 'points': 9999}, content_type='application/json', **authorization)
        self.assertNotEqual(forged.json()['identifier'], 'OK')
        self.assertEqual(BgmService.stats(self.progress)[0], 0)
