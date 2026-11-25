import json
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.core.management.base import CommandError
from django.utils import timezone
from smartdjango import Error

from Activity.management.commands.configure_record_shop import validate_manifest
from Activity.models import ActivityCampaign, ActivityEvent, ActivityService, RecordShopAnnouncement, RecordShopDiscovery, RecordShopPreference, RecordShopShare, SpaceActivity, UserActivityProgress
from Activity.record_shop import ROSTERS, SINGERS, award, bubble_preferences_payload, claim_singer, current_album_clip_path, decorate_message, finish_listen, played_complete, publish_announcement, register_share, resolve_shares, set_equipped_singer, space_first_album_medals, start_listen, track_identity
from Activity.record_shop_studio_catalog import STUDIO_ALBUMS
from Chat.models import Chat, ChatTypeChoice
from Message.models import LinkPreview, LinkPreviewStatusChoice, Message, MessageTypeChoice
from Space.models import Space
from Square.models import Statement
from User.models import User
from utils.auth import get_login_token, get_space_login_token


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
        self.assertEqual(track_identity({**pocklo, 'title': '未收录的歌曲'}, self.campaign.config), 'jay:未收录的歌曲')
        self.assertIsNone(track_identity({**pocklo, 'available': False}, self.campaign.config))
        self.assertIsNone(track_identity({**self.music, 'provider': 'apple_music'}, self.campaign.config))
        self.assertIsNone(track_identity({**self.music, 'artists': []}, self.campaign.config))

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

    def test_concurrent_editions_credit_a_share_and_apply_their_own_singer_rates(self):
        women = ActivityCampaign.objects.create(
            key='record-shop-women', title='回声唱片铺 · 女声篇', event_key='record-shop.share',
            duration_seconds=30 * 86400, config={'mode': 'record_shop', 'roster': 'women'},
        )
        ActivityService.claim_for_space(women, self.space, timezone.now() - timedelta(days=1))
        track = {**self.music, 'title': '暖暖', 'artists': ['梁靜茹']}
        self.assertEqual(track_identity(track, women.config), 'fish:暖暖')
        self.assertEqual(track_identity({**track, 'artists': ['Fish Leong'], 'provider': 'qq_music'}, women.config), 'fish:暖暖')
        url = 'https://music.163.com/song?id=1'
        preview = LinkPreview.objects.create(url=url, url_hash=LinkPreview.hash_url(url),
                                             status=LinkPreviewStatusChoice.READY, provider_data=track)
        source = SimpleNamespace(user=self.user, pk=8, created_at=timezone.now())
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                register_share(source, 'chat', preview)
        self.assertEqual(RecordShopShare.objects.filter(source_id=8).count(), 2)
        self.assertEqual(set(UserActivityProgress.objects.filter(user=self.user).values_list('earned_points', flat=True)), {5})
        now = timezone.now()
        with patch('Activity.record_shop.get_playback_source', return_value=track):
            body = dict(surface='chat', source_id=8, provider='netease_music', song_id='1')
            receipts = {key: start_listen(key, self.user, body)['receipt'] for key in (women.key, self.campaign.key)}
            with patch('Activity.record_shop.timezone.now', return_value=now + timedelta(seconds=299)):
                def listen(key, receipt):
                    return finish_listen(key, self.user, dict(
                        receipt=receipt, duration_ms=298_000, played=[[0, 298_000]], ended=True))
                self.assertEqual(listen(women.key, receipts[self.campaign.key]), {'points': 0})
                self.assertEqual(listen(women.key, receipts[women.key]), {'points': 6})
                self.assertEqual(listen(self.campaign.key, receipts[self.campaign.key]), {'points': 4})

    def test_earned_singer_can_be_equipped_across_editions_after_a_run_ends(self):
        women = ActivityCampaign.objects.create(
            key='record-shop-women', title='回声唱片铺 · 女声篇', event_key='record-shop.share',
            duration_seconds=30 * 86400, config={'mode': 'record_shop', 'roster': 'women'},
        )
        start = timezone.now()
        run = ActivityService.claim_for_space(women, self.space, start - timedelta(days=1))
        award(self.run, self.user, 'share', 'jay:track', 30)
        award(run, self.user, 'share', 'jolin:track', 30)
        self.assertEqual(claim_singer(self.campaign.key, self.user, 'jay')['equipped'], '')
        with self.assertRaises(Error):
            claim_singer(women.key, self.user, 'jay')
        # An already equipped singer (including data from older clients) remains in use.
        RecordShopPreference.objects.filter(progress__campaign=self.campaign, progress__user=self.user).update(equipped='jay')
        headers = {'HTTP_AUTHORIZATION': f"Bearer {get_login_token(self.user)['auth']}"}
        claimed = self.client.post(f'/activities/{women.key}/record-shop/claim', {'singer': 'jolin'},
                                   content_type='application/json', **headers).json()['body']
        self.assertEqual(claimed['singers'], ['jolin'])
        self.assertEqual(claimed['equipped'], '')
        self.assertEqual(list(RecordShopPreference.objects.exclude(equipped='').values_list('equipped', flat=True)), ['jay'])
        retried = self.client.post(f'/activities/{women.key}/record-shop/singer', {'singer': 'jolin'},
                                   content_type='application/json', **headers).json()['body']
        self.assertEqual(retried['equipped'], '')

        def personalize(**changes):
            return self.client.post('/users/me/personalization',
                                    {'chat_bubble_style': 'default', 'avatar_frame_style': 'none', **changes},
                                    content_type='application/json', **headers)

        with patch('Activity.record_shop.timezone.now', return_value=start + timedelta(days=31)):
            denial = personalize(record_shop_singer='stefanie', show_self_avatar=False)
            self.assertEqual(denial.status_code, 403)
            self.user.refresh_from_db()
            self.assertTrue(self.user.show_self_avatar)
            self.assertEqual(personalize(show_self_avatar=False).json()['body']['record_shop_bubbles'],
                             {'claimed': ['jay', 'jolin'], 'equipped': 'jay'})
            self.assertEqual(personalize(record_shop_singer='jolin').json()['body']['record_shop_bubbles'],
                             {'claimed': ['jay', 'jolin'], 'equipped': 'jolin'})
            self.assertEqual(list(RecordShopPreference.objects.exclude(equipped='').values_list('equipped', flat=True)), ['jolin'])
            self.assertEqual(personalize(record_shop_singer='').json()['body']['record_shop_bubbles'],
                             {'claimed': ['jay', 'jolin'], 'equipped': ''})
            self.assertEqual(personalize(record_shop_singer='jay').json()['body']['record_shop_bubbles']['equipped'], 'jay')
        self.assertEqual(bubble_preferences_payload(self.other), {'claimed': [], 'equipped': ''})
        self.assertEqual(RecordShopPreference.objects.get(progress__campaign=women).singers, ['jolin'])

    def test_only_two_claims_and_retry_is_free(self):
        with self.assertRaises(Error):
            claim_singer(self.campaign.key, self.user, 'jay')
        for index in range(3):
            award(self.run, self.user, 'share', f'jay:test{index}', 10)
        self.assertEqual(claim_singer(self.campaign.key, self.user, 'jay')['singers'], ['jay'])
        with self.assertRaises(Error):
            claim_singer(self.campaign.key, self.user, 'jj')
        self.assertEqual(claim_singer(self.campaign.key, self.user, 'jay')['equipped'], '')
        for index in range(3, 6):
            award(self.run, self.user, 'share', f'jay:test{index}', 10)
        self.assertEqual(claim_singer(self.campaign.key, self.user, 'jj')['singers'], ['jay', 'jj'])
        self.assertEqual(claim_singer(self.campaign.key, self.user, 'jay')['choices'], 0)

    def test_other_artist_shares_are_scored_once_across_platforms(self):
        tracks = [
            ('https://music.163.com/song?id=2', 'netease_music', '陳奕迅', '十年'),
            ('https://y.qq.com/n/ryqq/songDetail/test', 'qq_music', '陈奕迅', '十年 (feat. 演唱嘉宾)'),
        ]
        for index, (url, provider, artist, title) in enumerate(tracks):
            preview = LinkPreview.objects.create(url=url, url_hash=LinkPreview.hash_url(url), status=LinkPreviewStatusChoice.READY,
                provider_data={**self.music, 'song_id': str(index), 'provider': provider, 'artists': [artist], 'title': title})
            for source, actor, surface in [(index * 10 + 1, self.user, 'chat'), (index * 10 + 2, self.other, 'square')]:
                RecordShopShare.objects.create(run=self.run, user=actor, preview=preview, source_id=source,
                                               surface=surface, published_at=timezone.now())
            resolve_shares(preview.pk)
        self.assertEqual(UserActivityProgress.objects.get(user=self.user, campaign=self.campaign).earned_points, 5)
        self.assertEqual(UserActivityProgress.objects.get(user=self.other, campaign=self.campaign).earned_points, 10)

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
        claim_singer(self.campaign.key, self.user, 'jay')
        set_equipped_singer(self.user, 'jay')
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

    def test_square_medals_show_only_the_spaces_first_rare_discoveries(self):
        now = timezone.now()
        self.space.admin_phone_verified_at = now
        self.space.group_square_enabled = True
        self.space.save(update_fields=['admin_phone_verified_at', 'group_square_enabled'])
        self.campaign.config['albums'] = {
            'jay': [
                {'id': tier, 'tier': tier, 'name': tier, 'title': 'A song',
                 'cover': 'https://example.test/cover.jpg', 'clip': 'https://example.test/clip.m4a'}
                for tier in ('gold', 'diamond', 'obsidian')
            ],
        }
        self.campaign.save(update_fields=['config'])
        other_space = Space.objects.create(
            name='Other records', slug='other-records', email='other@example.test',
            group_square_enabled=True, admin_phone_verified_at=now,
        )
        outsider = User.create(other_space, 'Outsider', email='outside@example.test', verified=True)
        other_run = ActivityService.claim_for_space(self.campaign, other_space, now - timedelta(days=1))
        RecordShopAnnouncement.objects.create(run=other_run, album_id='diamond', user=outsider, discovered_at=now)
        RecordShopAnnouncement.objects.create(run=self.run, album_id='diamond', user=self.other,
                                             discovered_at=now - timedelta(hours=1))
        RecordShopAnnouncement.objects.create(run=self.run, album_id='gold', user=self.user, discovered_at=now)
        RecordShopAnnouncement.objects.create(run=self.run, album_id='obsolete', user=self.user, discovered_at=now)
        RecordShopAnnouncement.objects.create(run=self.run, album_id='obsidian', user=self.user, discovered_at=now)

        auth = {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(self.user)['auth']}
        self.run.ends_at = now - timedelta(minutes=1)
        self.run.save(update_fields=['ends_at'])
        response = self.client.get('/square/medals', **auth)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        medals = response.json()['body']
        self.assertEqual([item['album']['tier'] for item in medals], ['obsidian', 'diamond'])
        self.assertEqual([item['user']['name'] for item in medals], [self.user.name, self.other.name])
        self.assertEqual(medals[0]['discovered_at'], now.timestamp())
        self.assertEqual(medals[0]['artist'], 'jay')
        self.assertNotIn('clip', medals[0]['album'])
        self.assertNotIn('email', medals[0]['user'])
        self.assertFalse(RecordShopAnnouncement.objects.filter(published=True).exists())

        self.space.group_square_enabled = False
        self.space.save(update_fields=['group_square_enabled'])
        self.assertNotEqual(self.client.get('/square/medals', **auth).json()['body'], medals)

    def test_full_short_audio_is_six_points(self):
        music = {**self.music, 'duration_ms': 20_000}
        key = self.campaign.key
        started = timezone.now()
        with patch('Activity.record_shop.get_playback_source', return_value=music):
            receipt = start_listen(key, self.user, dict(surface='square', source_id=5, provider='netease_music', song_id='1'))['receipt']
            packet = dict(receipt=receipt, duration_ms=20_000, played=[[0, 20_000]], ended=True)
            with patch('Activity.record_shop.timezone.now', return_value=started + timedelta(seconds=21)):
                self.assertEqual(finish_listen(key, self.user, packet), {'points': 6})

    def test_complete_listen_scores_follow_artist_and_preview_status(self):
        key = self.campaign.key
        started = timezone.now()
        cases = [
            ('Jay Chou', '周杰伦整首', 182_000, 6),
            ('陈奕迅', '普通歌曲整首', 182_000, 4),
            ('陈奕迅', '普通歌曲试听', 30_000, 2),
        ]
        for index, (artist, title, duration, points) in enumerate(cases, start=2):
            with self.subTest(artist=artist, duration=duration):
                music = {**self.music, 'song_id': index, 'artists': [artist], 'title': title, 'duration_ms': 182_000}
                with patch('Activity.record_shop.get_playback_source', return_value=music):
                    receipt = start_listen(key, self.user, dict(surface='chat', source_id=5, provider='netease_music', song_id=str(index)))['receipt']
                    packet = dict(receipt=receipt, duration_ms=duration, played=[[0, duration]], ended=True)
                    with patch('Activity.record_shop.timezone.now', return_value=started + timedelta(seconds=183)):
                        self.assertEqual(finish_listen(key, self.user, packet), {'points': points})
                        self.assertEqual(finish_listen(key, self.user, packet), {'points': 0})

    @staticmethod
    def example_manifest():
        return {
            'portraits': {artist: 'https://example.test/portrait.webp' for artist in SINGERS},
            'verified_titles': {artist: ['示例曲'] for artist in SINGERS},
            'albums': {artist: [
                dict(id=f'{artist}-{tier}', tier=tier, name='示例专辑', title='示例曲',
                     cover='https://example.test/cover.webp', clip=f'https://example.test/{artist}-{tier}.m4a', probability=probability)
                for tier, probability in [('gold', .59952), ('platinum', .29976), ('diamond', .09992), ('obsidian', .0008)]
            ] for artist in SINGERS},
        }

    def test_manifest_requires_audio_files_instead_of_streaming_pages(self):
        manifest = self.example_manifest()
        self.assertEqual(validate_manifest(manifest)['mode'], 'record_shop')
        manifest['albums']['jay'][0]['clip'] = 'https://example.test/resources/assets/10/display/' + 'a' * 64
        self.assertEqual(validate_manifest(manifest)['mode'], 'record_shop')
        manifest['albums']['jay'][0]['clip'] = 'https://music.apple.com/cn/song/123'
        with self.assertRaisesMessage(CommandError, 'Missing clipped recordings for 1 albums: jay-gold'):
            validate_manifest(manifest)

    def test_replacing_clips_preserves_running_activity_and_album_data(self):
        manifest = self.example_manifest()
        self.campaign.config = validate_manifest(manifest)
        self.campaign.save(update_fields=['config'])
        award(self.run, self.user, 'share', 'jay:track', 10)
        replacement = 'https://example.test/resources/assets/11/display/' + 'c' * 64
        manifest['albums']['jay'][0]['clip'] = replacement
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps(manifest))
            call_command('configure_record_shop', str(path), replace_clips=True)
            self.campaign.refresh_from_db()
            self.assertEqual(self.campaign.config['albums']['jay'][0]['clip'], replacement)
            self.assertEqual(SpaceActivity.objects.get(pk=self.run.pk).ends_at, self.run.ends_at)
            self.assertEqual(UserActivityProgress.objects.get(campaign=self.campaign, user=self.user).earned_points, 10)
            manifest['albums']['jay'][0]['cover'] = 'https://example.test/other.webp'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesMessage(CommandError, 'Clip replacement must preserve every album'):
                call_command('configure_record_shop', str(path), replace_clips=True)

    def test_studio_refresh_preserves_medals_and_old_audio_when_catalog_is_pruned(self):
        old_clip = 'https://example.test/resources/assets/11/display/' + 'c' * 64
        for edition, key in [('classic', self.campaign.key), ('women', 'record-shop-women')]:
            config = {
                'mode': 'record_shop',
                'roster': edition,
                'portraits': {artist: 'https://example.test/portrait.webp' for artist in ROSTERS[edition]},
                'verified_titles': {artist: ['Track'] for artist in ROSTERS[edition]},
                'albums': {artist: [
                    dict(id=album_id, name=album_id, title='Track', tier='diamond', probability=1,
                         cover='https://example.test/cover.webp', clip=old_clip)
                    for tier in STUDIO_ALBUMS[artist] for album_id in tier
                ] for artist in ROSTERS[edition]},
            }
            if edition == 'classic':
                config['albums']['jay'].append({**config['albums']['jay'][0], 'id': 'jay-world-tour', 'tier': 'obsidian'})
                self.campaign.config = config
                self.campaign.save(update_fields=['config'])
            else:
                ActivityCampaign.objects.create(key=key, title='Women', event_key='record-shop.share', config=config)

        for album_id in ('jay-world-tour', 'jay-chopin'):
            RecordShopAnnouncement.objects.create(run=self.run, album_id=album_id, user=self.user, discovered_at=timezone.now())
        award(self.run, self.user, 'share', 'jay:track', 30)
        claim_singer(self.campaign.key, self.user, 'jay')
        set_equipped_singer(self.user, 'jay')
        progress = UserActivityProgress.objects.get(user=self.user, campaign=self.campaign)
        RecordShopDiscovery.objects.create(progress=progress, album_id='jay-world-tour', discovered_at=timezone.now())

        call_command('refresh_record_shop_catalog')
        self.assertEqual(RecordShopAnnouncement.objects.filter(album_snapshot__isnull=True).count(), 2)
        for _ in range(2):
            call_command('refresh_record_shop_catalog', apply=True)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.config['albums']['jay'][0]['id'], 'jay-fantasy')
        self.assertNotIn('jay-world-tour', [row['id'] for row in self.campaign.config['albums']['jay']])
        self.assertEqual(current_album_clip_path('jay-world-tour'), '/resources/assets/11/display/' + 'c' * 64)
        self.assertEqual(SpaceActivity.objects.get(pk=self.run.pk).ends_at, self.run.ends_at)
        self.assertEqual(UserActivityProgress.objects.get(pk=progress.pk).earned_points, 30)
        self.assertTrue(RecordShopDiscovery.objects.filter(progress=progress, album_id='jay-world-tour').exists())
        medals = space_first_album_medals(self.space.pk)
        self.assertEqual({row['album']['id']: row['album']['tier'] for row in medals},
                         {'jay-world-tour': 'obsidian', 'jay-chopin': 'diamond'})
        women = ActivityCampaign.objects.get(key='record-shop-women')
        self.assertEqual([len(women.config['albums'][artist]) for artist in ROSTERS['women']], [16, 12, 15, 14])

    def test_a_later_rare_draw_earns_space_medal_without_replaying_personal_first_discovery(self):
        album = dict(id='jay-studio', tier='diamond', name='Studio', title='Track', probability=1,
                     cover='https://example.test/cover.webp', clip='https://example.test/song.m4a')
        self.campaign.config.update(albums={'jay': [album]}, portraits={'jay': 'https://example.test/portrait.webp'})
        self.campaign.save(update_fields=['config'])
        award(self.run, self.user, 'share', 'jay:track', 30)
        claim_singer(self.campaign.key, self.user, 'jay')
        set_equipped_singer(self.user, 'jay')
        progress = UserActivityProgress.objects.get(user=self.user, campaign=self.campaign)
        RecordShopDiscovery.objects.create(progress=progress, album_id=album['id'], discovered_at=timezone.now())
        chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=self.user)
        for text in ('First', 'Second', 'Third'):
            message = Message.objects.create(chat=chat, user=self.user, type=MessageTypeChoice.TEXT, content=text)
            decorate_message(message)
        self.assertFalse(message.appearance['record_shop']['first'])
        self.assertEqual(message.appearance['record_shop']['album']['tier'], 'diamond')
        self.assertEqual(RecordShopDiscovery.objects.filter(progress=progress).count(), 1)
        self.assertEqual(RecordShopAnnouncement.objects.get(run=self.run).album_snapshot['album']['tier'], 'diamond')

    def test_space_admin_claim_starts_one_thirty_day_run(self):
        self.campaign.assignment_mode = ActivityCampaign.AssignmentMode.MANUAL
        self.campaign.save(update_fields=['assignment_mode'])
        space = Space.objects.create(name='Local music test', slug='record-claim', email='claim@example.test')
        admin = {'HTTP_AUTHORIZATION': 'Bearer ' + get_space_login_token(space)['auth']}
        listener = {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(self.user)['auth']}
        endpoint = f'/activities/admin/{self.campaign.key}/claim'

        available = self.client.get('/activities/admin', **admin).json()['body']
        activity = next(row for row in available if row['key'] == self.campaign.key)
        self.assertEqual(activity['status'], 'unclaimed')
        self.assertFalse(activity['mandatory'])
        self.assertNotEqual(self.client.post(endpoint, **listener).status_code, 200)
        self.assertFalse(SpaceActivity.objects.filter(space=space, campaign=self.campaign).exists())

        for _ in range(2):
            response = self.client.post(endpoint, **admin)
            self.assertEqual(response.status_code, 200, response.content)
        run = SpaceActivity.objects.get(space=space, campaign=self.campaign)
        self.assertEqual(run.ends_at - run.claimed_at, timedelta(days=30))
        self.assertTrue(run.is_active())
