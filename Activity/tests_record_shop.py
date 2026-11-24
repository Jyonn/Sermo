from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.core.management.base import CommandError
from django.utils import timezone
from smartdjango import Error

from Activity.management.commands.configure_record_shop import validate_manifest
from Activity.models import ActivityCampaign, ActivityEvent, ActivityService, RecordShopAnnouncement, RecordShopDiscovery, RecordShopShare, UserActivityProgress
from Activity.record_shop import SINGERS, award, decorate_message, finish_listen, played_complete, publish_announcement, resolve_shares, select_singer, start_listen, track_identity
from Chat.models import Chat, ChatTypeChoice
from Message.models import LinkPreview, LinkPreviewStatusChoice, Message, MessageTypeChoice
from Space.models import Space
from Square.models import Statement
from User.models import User


class RecordShopTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Record Shop', slug='record-shop-test', email='records@example.test')
        self.user = User.create(self.space, 'Listener', email='listener@example.test', verified=True)
        self.other = User.create(self.space, 'Listener Two', email='another-listener@example.test', verified=True)
        self.campaign = ActivityCampaign.objects.create(
            key='record-shop', title='回声唱片铺', event_key='record-shop.share',
            duration_seconds=30 * 86400, config={'mode': 'record_shop', 'verified_titles': {'jay': ['七里香']}},
        )
        self.run = ActivityService.claim_for_space(self.campaign, self.space, timezone.now() - timedelta(days=1))
        self.music = dict(provider='netease_music', song_id=1, title='七里香', artists=['周杰倫'], duration_ms=298_000)

    def test_provider_and_script_normalization(self):
        self.assertEqual(track_identity(self.music, self.campaign.config), 'jay:七里香')
        self.assertEqual(track_identity({**self.music, 'artists': ['Jay Chou'], 'provider': 'qq_music'}, self.campaign.config), 'jay:七里香')
        pocklo = {**self.music, 'provider': 'pocklo_music', 'available': True}
        self.assertEqual(track_identity(pocklo, self.campaign.config), 'jay:七里香')
        self.assertIsNone(track_identity({**pocklo, 'title': '自定义文件'}, self.campaign.config))
        self.assertIsNone(track_identity({**pocklo, 'available': False}, self.campaign.config))
        self.assertIsNone(track_identity({**self.music, 'provider': 'apple_music'}, self.campaign.config))

    def test_cached_preview_credits_publishers_once_with_separate_listens(self):
        url = 'https://music.163.com/song?id=1'
        preview = LinkPreview.objects.create(url=url, url_hash=LinkPreview.hash_url(url), status=LinkPreviewStatusChoice.PENDING)
        for source, actor, surface in [(5, self.user, 'square'), (8, self.user, 'chat'), (9, self.other, 'chat')]:
            RecordShopShare.objects.create(run=self.run, user=actor, preview=preview, source_id=source,
                                           surface=surface, published_at=timezone.now())
        resolve_shares(preview.pk)
        self.assertFalse(ActivityEvent.objects.filter(campaign=self.campaign).exists())
        preview.status = LinkPreviewStatusChoice.READY
        preview.provider_data = self.music
        preview.save(update_fields=['status', 'provider_data'])
        resolve_shares(preview.pk)
        resolve_shares(preview.pk)
        self.assertEqual(award(self.run, self.user, 'listen', 'jay:七里香', 6), 6)
        self.assertEqual(award(self.run, self.user, 'listen', 'jay:七里香', 6), 0)
        self.assertEqual(UserActivityProgress.objects.get(user=self.user, campaign=self.campaign).earned_points, 16)
        self.assertEqual(UserActivityProgress.objects.get(user=self.other, campaign=self.campaign).earned_points, 5)

    def test_only_twice_unlocks_and_reequip_is_free(self):
        with self.assertRaises(Error):
            select_singer(self.campaign.key, self.user, 'jay')
        for index in range(3):
            award(self.run, self.user, 'share', f'jay:test{index}', 10)
        self.assertEqual(select_singer(self.campaign.key, self.user, 'jay')['singers'], ['jay'])
        with self.assertRaises(Error):
            select_singer(self.campaign.key, self.user, 'jj')
        self.assertEqual(select_singer(self.campaign.key, self.user, 'jay')['equipped'], 'jay')
        for index in range(3, 6):
            award(self.run, self.user, 'share', f'jay:test{index}', 10)
        self.assertEqual(select_singer(self.campaign.key, self.user, 'jj')['singers'], ['jay', 'jj'])
        self.assertEqual(select_singer(self.campaign.key, self.user, 'jay')['choices'], 0)

    def test_partial_playback_never_scores(self):
        self.assertTrue(played_complete(30_000, [[12_000, 30_000], [0, 12_000]]))
        self.assertFalse(played_complete(30_000, [[0, 3000], [9000, 30_000]]))
        self.assertFalse(played_complete(30_000, [[0, 6000], [0, 6000], [24_000, 30_000]]))
        self.assertFalse(played_complete(30_000, [[100, 30_000]]))

    def test_listen_receipt_checks_time_identity_and_vip_duration(self):
        key = self.campaign.key
        started = timezone.now()
        with patch('Activity.record_shop.get_playback_source', return_value=self.music):
            receipt = start_listen(key, self.user, dict(surface='chat', source_id=5, provider='netease_music', song_id='1'))['receipt']
            packet = dict(receipt=receipt, duration_ms=30_000, played=[[0, 30_000]], ended=True)
            self.assertEqual(finish_listen(key, self.user, packet), {'points': 0})
            with patch('Activity.record_shop.timezone.now', return_value=started + timedelta(seconds=31)):
                self.assertEqual(finish_listen(key, self.other, packet), {'points': 0})
                self.assertEqual(finish_listen(key, self.user, packet), {'points': 3})
                self.assertEqual(finish_listen(key, self.user, packet), {'points': 0})

    def test_first_cover_is_stored_and_space_announcement_is_idempotent(self):
        self.space.admin_phone_verified_at = timezone.now()
        self.space.group_square_enabled = True
        self.space.save(update_fields=['admin_phone_verified_at', 'group_square_enabled'])
        self.campaign.config.update(
            albums={'jay': [dict(id='rare-album', tier='obsidian', name='珍藏', title='示例曲',
                                probability=1, cover='https://example.test/cover.jpg', clip='https://example.test/clip.m4a')]},
            portraits={'jay': 'https://example.test/test.webp'},
        )
        self.campaign.save(update_fields=['config'])
        award(self.run, self.user, 'share', 'jay:testing', 30)
        select_singer(self.campaign.key, self.user, 'jay')
        chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=self.user)
        first = Message.objects.create(chat=chat, user=self.user, type=MessageTypeChoice.TEXT, content='第一条')
        decorate_message(first)
        self.assertNotIn('album', first.appearance['record_shop'])
        for expected in (True, False):
            message = Message.objects.create(chat=chat, user=self.user, type=MessageTypeChoice.TEXT, content='第二条')
            decorate_message(message)
            message.refresh_from_db()
            self.assertEqual(message.appearance['record_shop']['first'], expected)
            self.assertEqual(message.appearance['record_shop']['album']['id'], 'rare-album')
        self.assertEqual(RecordShopDiscovery.objects.count(), 1)
        announcement = RecordShopAnnouncement.objects.get()
        publish_announcement(announcement.pk)
        publish_announcement(announcement.pk)
        announcement.refresh_from_db()
        self.assertTrue(announcement.published)
        self.assertTrue(announcement.statement.user.is_official)
        self.assertIn(self.user.name, announcement.statement.text)
        self.assertEqual(Statement.objects.filter(space=self.space).count(), 1)

    def test_full_short_audio_is_six_points(self):
        music = {**self.music, 'duration_ms': 20_000}
        key = self.campaign.key
        started = timezone.now()
        with patch('Activity.record_shop.get_playback_source', return_value=music):
            receipt = start_listen(key, self.user, dict(surface='square', source_id=5, provider='netease_music', song_id='1'))['receipt']
            packet = dict(receipt=receipt, duration_ms=20_000, played=[[0, 20_000]], ended=True)
            with patch('Activity.record_shop.timezone.now', return_value=started + timedelta(seconds=21)):
                self.assertEqual(finish_listen(key, self.user, packet), {'points': 6})

    def test_manifest_requires_audio_files_instead_of_streaming_pages(self):
        manifest = {
            'portraits': {artist: 'https://example.test/portrait.webp' for artist in SINGERS},
            'verified_titles': {artist: ['示例曲'] for artist in SINGERS},
            'albums': {artist: [
                dict(id=f'{artist}-{tier}', tier=tier, name='示例专辑', title='示例曲',
                     cover='https://example.test/cover.webp', clip=f'https://example.test/{artist}-{tier}.m4a', probability=probability)
                for tier, probability in [('gold', .59952), ('platinum', .29976), ('diamond', .09992), ('obsidian', .0008)]
            ] for artist in SINGERS},
        }
        self.assertEqual(validate_manifest(manifest)['mode'], 'record_shop')
        manifest['albums']['jay'][0]['clip'] = 'https://music.apple.com/cn/song/123'
        with self.assertRaisesMessage(CommandError, 'Missing clipped recordings for 1 albums: jay-gold'):
            validate_manifest(manifest)
