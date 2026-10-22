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

    def test_peer_autoplay_preference_is_saved_and_preserved(self):
        data = BgmService.configure(self.campaign, self.user, {'style': 'blue', 'receive_audio': False})['bgm']
        self.assertFalse(data['receive_audio'])
        self.assertFalse(self.join()['bgm']['receive_audio'])
        self.assertTrue(BgmService.configure(self.campaign, self.user, {'style': 'blue', 'receive_audio': True})['bgm']['receive_audio'])

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

    def record_day(self, day, accelerate=False):
        with patch('Activity.bgm.timezone.localdate', return_value=day):
            BgmService.record(self.user, 'publish', self.statement(self.user))
            if accelerate:
                target = self.statement()
                comment = StatementComment.objects.create(statement=target, user=self.user, text='A thoughtful response')
                BgmService.record(self.user, 'comment', comment)
                BgmService.record(self.user, 'like', target)

    def test_daily_posts_collect_every_track_on_day_fifteen(self):
        today = timezone.localdate(timezone=BgmService.ZONE)
        for day in range(14):
            self.record_day(today - timedelta(days=day))
        tracks = BgmService.payload(self.campaign, self.user, self.run)['bgm']['tracks']
        self.assertFalse(tracks[-1]['owned'])
        self.record_day(today - timedelta(days=14))
        self.assertEqual(BgmService.stats(self.progress)[:2], (90, 15))
        self.assertTrue(all(track['owned'] for track in BgmService.payload(self.campaign, self.user, self.run)['bgm']['tracks']))

    def test_comments_and_likes_accelerate_to_ten_days_and_allow_playback(self):
        today = timezone.localdate(timezone=BgmService.ZONE)
        for day in range(9):
            self.record_day(today - timedelta(days=day), accelerate=True)
        self.assertEqual(BgmService.stats(self.progress)[:2], (90, 9))
        self.assertFalse(BgmService.payload(self.campaign, self.user, self.run)['bgm']['tracks'][-1]['owned'])
        self.record_day(today - timedelta(days=9), accelerate=True)
        self.assertTrue(all(track['owned'] for track in BgmService.payload(self.campaign, self.user, self.run)['bgm']['tracks']))
        BgmService.configure(self.campaign, self.user, {'style': 'blue', 'slots': ['12'], 'mode': 'manual'})
        result = BgmService.enter(self.campaign, self.user, {'chat_id': self.chat(self.user).id, 'request_id': 'accelerated-001', 'track': '12'})
        self.assertTrue(result['sent'])

    def test_new_gates_preserve_all_previously_unlocked_tracks(self):
        previous = [(0, 0), (1, 10), (2, 25), (3, 40), (4, 60), (5, 80),
                    (7, 105), (9, 130), (11, 155), (12, 180), (14, 210), (15, 240)]
        for old, new in zip(previous, BgmService.GATES):
            self.assertLessEqual(new[0], old[0])
            self.assertLessEqual(new[1], old[1])

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

    def test_entrance_creates_only_one_system_message_with_playback_and_cooldown(self):
        from Activity.models import BgmEntrance
        from Message.models import Message, MessageEvent, MessageTypeChoice
        chat = self.chat(self.user)
        BgmService.configure(self.campaign, self.user, {'style': 'blue', 'mode': 'manual'})
        response = BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0001', 'track': '1'})
        self.assertTrue(response['sent'])
        self.assertIsNone(response['audio_message_id'])
        system = Message.objects.get(pk=response['system_message_id'])
        self.assertEqual(Message.objects.filter(chat=chat).count(), 1)
        self.assertEqual(MessageEvent.objects.filter(chat=chat).count(), 1)
        self.assertFalse(Message.objects.filter(chat=chat, type=MessageTypeChoice.AUDIO).exists())
        self.assertIsNotNone(system.media_resource_id)
        self.assertEqual(system.media_resource.reference_count, 1)
        payload = system._payload_for_type()
        self.assertTrue(payload['bgm_entrance'])
        self.assertEqual(payload['kind'], 'system')
        self.assertEqual(payload['uri'], response['uri'])
        self.assertEqual(payload['duration_seconds'], response['duration'])
        self.assertEqual(payload['song_title'], response['title'])
        self.assertIsNone(BgmEntrance.objects.get(system_message_id=system.id).audio_message_id)
        self.assertEqual(system.type, MessageTypeChoice.SYSTEM)
        self.assertIn('Track 0', system.system_message_text(self.user))
        self.assertEqual(BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0001', 'track': '1'})['reason'], 'duplicate')
        self.assertEqual(BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'request-0002', 'track': '1'})['reason'], 'cooldown')
        self.assertEqual(Message.objects.filter(chat=chat).count(), 1)
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

    def test_cooldown_is_per_user_per_chat_and_expires_at_three_minutes(self):
        from Chat.models import ChatMember
        from Activity.models import BgmEntrance
        chat = self.chat(self.user)
        ChatMember.objects.create(chat=chat, user=self.peer, status=1)
        BgmService.configure(self.campaign, self.user, {'style': 'blue', 'mode': 'manual'})
        BgmService.configure(self.campaign, self.peer, {'style': 'pink', 'mode': 'manual'})
        send = lambda actor, room, rid: BgmService.enter(self.campaign, actor, {'chat_id': room.id, 'request_id': rid, 'track': '1'})
        self.assertTrue(send(self.user, chat, 'first-user-001')['sent'])
        self.assertTrue(send(self.peer, chat, 'second-user-001')['sent'])
        self.assertTrue(send(self.user, self.chat(self.user), 'other-chat-001')['sent'])
        now = timezone.now()
        BgmEntrance.objects.filter(participation__progress=self.progress, chat_id=chat.id).update(created_at=now - timedelta(seconds=179))
        with patch('Activity.bgm.timezone.now', return_value=now):
            self.assertEqual(BgmService.entrance_status(self.user, chat.id)['retry_after_seconds'], 1)
            self.assertEqual(send(self.user, chat, 'too-soon-001')['retry_after_seconds'], 1)
        with patch('Activity.bgm.timezone.now', return_value=now + timedelta(seconds=1)):
            self.assertTrue(BgmService.entrance_status(self.user, chat.id)['allowed'])
            self.assertTrue(send(self.user, chat, 'after-three-001')['sent'])

    def test_preflight_is_authenticated_scoped_and_never_sends(self):
        from utils import auth
        from Activity.models import BgmEntrance
        authorization = {'HTTP_AUTHORIZATION': f'Bearer {auth.get_login_token(self.user)["auth"]}'}
        chat = self.chat(self.user)
        BgmService.configure(self.campaign, self.user, {'style': 'blue', 'mode': 'auto'})
        response = self.client.get(f'/activities/bgm/me?chat_id={chat.id}', **authorization).json()['body']
        self.assertTrue(response['bgm']['entrance']['allowed'])
        self.assertEqual(BgmEntrance.objects.count(), 0)
        BgmService.enter(self.campaign, self.user, {'chat_id': chat.id, 'request_id': 'preflight-001'})
        response = self.client.get(f'/activities/bgm/me?chat_id={chat.id}', **authorization).json()['body']
        self.assertFalse(response['bgm']['entrance']['allowed'])
        self.assertGreater(response['bgm']['entrance']['retry_after_seconds'], 0)
        self.assertEqual(BgmEntrance.objects.count(), 1)
        foreign = self.chat(self.peer)
        self.assertNotEqual(self.client.get(f'/activities/bgm/me?chat_id={foreign.id}', **authorization).json()['identifier'], 'OK')

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
