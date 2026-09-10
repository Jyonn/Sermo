from unittest.mock import patch
from types import SimpleNamespace

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat
from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from Sticker.models import StickerAsset, UserSticker, UserStickerUsage
from User.models import User
from utils import auth


class StickerAssetDimensionTests(TestCase):
    @patch('Sticker.models.avatar_uri_for_key', return_value='https://example.test/sticker.png')
    @patch('Message.image_metadata.fetch_qiniu_image_info')
    def test_refresh_dimensions_saves_qiniu_image_info(self, image_info, _source_uri):
        image_info.return_value = {'size': 4096, 'width': 360, 'height': 240}
        asset = StickerAsset.objects.create(
            content_hash='a' * 64,
            storage_key='sermo/messages/sticker/example.png',
            file_size=1024,
        )

        StickerAsset.refresh_dimensions(asset)

        asset.refresh_from_db()
        self.assertEqual(asset.file_size, 4096)
        self.assertEqual(asset.pixel_width, 360)
        self.assertEqual(asset.pixel_height, 240)
        self.assertIsNotNone(asset.dimensions_checked_at)
        self.assertEqual(asset.dimensions_error, '')

    @patch('Sticker.models.avatar_uri_for_key', return_value='https://example.test/sticker.webp')
    @patch('Message.image_metadata.fetch_qiniu_image_info', side_effect=RuntimeError('temporary failure'))
    def test_refresh_dimensions_records_retryable_error(self, _image_info, _source_uri):
        asset = StickerAsset.objects.create(
            content_hash='b' * 64,
            storage_key='sermo/messages/sticker/example.webp',
        )

        StickerAsset.refresh_dimensions(asset)

        asset.refresh_from_db()
        self.assertIsNone(asset.pixel_width)
        self.assertIsNone(asset.pixel_height)
        self.assertIsNotNone(asset.dimensions_checked_at)
        self.assertEqual(asset.dimensions_error, 'temporary failure')


class StickerExplorePrivacyTests(TestCase):
    def setUp(self):
        self.asset = StickerAsset.objects.create(
            content_hash='c' * 64,
            storage_key='sermo/messages/sticker/privacy.png',
        )

    def test_same_space_source_exposes_only_public_identity(self):
        source = SimpleNamespace(
            space_id=7,
            tiny_json=lambda: {
                'user_id': 99,
                'name': '同空间用户',
                'avatar_uri': 'https://example.test/avatar.png',
                'official': False,
            },
        )
        request = SimpleNamespace(user=SimpleNamespace(space_id=7), build_absolute_uri=lambda path: f'https://api.test{path}')

        payload = self.asset.jsonl(request=request, source_user=source)

        self.assertEqual(payload['source_scope'], 'local')
        self.assertEqual(payload['source_user'], {
            'name': '同空间用户',
            'avatar_uri': 'https://example.test/avatar.png',
        })
        self.assertNotIn('user_id', payload['source_user'])

    def test_other_space_source_is_anonymous(self):
        source = SimpleNamespace(space_id=8, tiny_json=lambda: {'name': '不应泄露', 'avatar_uri': 'secret'})
        request = SimpleNamespace(user=SimpleNamespace(space_id=7), build_absolute_uri=lambda path: f'https://api.test{path}')

        payload = self.asset.jsonl(request=request, source_user=source)

        self.assertEqual(payload['source_scope'], 'external')
        self.assertNotIn('source_user', payload)


class StickerPaginationTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Sticker space',
            slug='sticker-space',
            email='admin@example.com',
            admin_phone_verified_at=timezone.now(),
        )
        self.user = User.create(self.space, 'Collector', email='collector@example.com', verified=True)
        self.other = User.create(self.space, 'Source', email='source@example.com', verified=True)

    def authorization(self):
        return dict(HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.user)['auth']}")

    def create_asset(self, index):
        return StickerAsset.objects.create(
            content_hash=f'{index:064x}',
            storage_key=f'sermo/messages/sticker/{index}.png',
        )

    def create_direct_chat(self):
        user_low, user_high = sorted((self.user, self.other), key=lambda user: user.id)
        Friendship.objects.get_or_create(
            space=self.space,
            user_low=user_low,
            user_high=user_high,
            defaults=dict(
                requested_by=self.user,
                status=FriendshipStatusChoice.ACCEPTED,
            ),
        )
        return Chat.get_or_create_direct(self.user, self.other)

    def test_my_stickers_return_stable_pages(self):
        for index in range(5):
            UserSticker.objects.create(user=self.user, asset=self.create_asset(index + 1))

        first = self.client.get('/stickers/?offset=0&limit=2', **self.authorization()).json()['body']
        second = self.client.get(
            f"/stickers/?offset={first['next_offset']}&limit=2",
            **self.authorization(),
        ).json()['body']

        self.assertEqual(len(first['items']), 2)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['next_offset'], 2)
        self.assertEqual(len(second['items']), 2)
        self.assertTrue(set(item['sticker_id'] for item in first['items']).isdisjoint(
            item['sticker_id'] for item in second['items']
        ))

    def test_explore_stickers_return_stable_pages(self):
        for index in range(5):
            UserSticker.objects.create(user=self.other, asset=self.create_asset(index + 10))

        first = self.client.get('/stickers/explore?offset=0&limit=2', **self.authorization()).json()['body']
        second = self.client.get(
            f"/stickers/explore?offset={first['next_offset']}&limit=2",
            **self.authorization(),
        ).json()['body']

        self.assertEqual(len(first['items']), 2)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['items']), 2)
        self.assertTrue(set(item['sticker_asset_id'] for item in first['items']).isdisjoint(
            item['sticker_asset_id'] for item in second['items']
        ))

    @patch('Sticker.services.delete_sticker_file')
    def test_removing_last_collection_deletes_asset_referenced_only_by_deleted_messages(self, delete_file):
        asset = self.create_asset(20)
        sticker = UserSticker.objects.create(user=self.user, asset=asset)
        chat = self.create_direct_chat()
        message = Message.create(
            chat=chat,
            user=self.user,
            message_type=MessageTypeChoice.STICKER,
            content=f'{{"asset_id":{asset.id}}}',
        )
        message.is_deleted = True
        message.save(update_fields=['is_deleted'])

        response = self.client.delete(
            f'/stickers/?sticker_id={sticker.id}',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(StickerAsset.objects.filter(id=asset.id).exists())
        delete_file.assert_called_once_with(asset.storage_key)

    @patch('Sticker.services.delete_sticker_file')
    def test_removing_last_collection_keeps_asset_with_active_message(self, delete_file):
        asset = self.create_asset(21)
        sticker = UserSticker.objects.create(user=self.user, asset=asset)
        chat = self.create_direct_chat()
        Message.create(
            chat=chat,
            user=self.user,
            message_type=MessageTypeChoice.STICKER,
            content=f'{{"asset_id":{asset.id}}}',
        )

        response = self.client.delete(
            f'/stickers/?sticker_id={sticker.id}',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(StickerAsset.objects.filter(id=asset.id).exists())
        delete_file.assert_not_called()

    @patch('Sticker.services.delete_sticker_file')
    def test_cleanup_command_backfills_orphaned_assets(self, delete_file):
        orphan = self.create_asset(22)
        retained = self.create_asset(23)
        UserSticker.objects.create(user=self.other, asset=retained)

        call_command('cleanup_orphaned_stickers', dry_run=True, verbosity=0)
        self.assertTrue(StickerAsset.objects.filter(id=orphan.id).exists())

        call_command('cleanup_orphaned_stickers', batch_size=1, verbosity=0)

        self.assertFalse(StickerAsset.objects.filter(id=orphan.id).exists())
        self.assertTrue(StickerAsset.objects.filter(id=retained.id).exists())
        delete_file.assert_called_once_with(orphan.storage_key)

    def test_explore_returns_recently_frequent_stickers_without_double_counting_retries(self):
        asset = self.create_asset(30)
        UserSticker.objects.create(user=self.other, asset=asset)
        chat = self.create_direct_chat()

        for _index in range(2):
            Message.create(
                chat=chat,
                user=self.user,
                message_type=MessageTypeChoice.STICKER,
                content=f'{{"asset_id":{asset.id}}}',
                client_message_id='sticker-usage-idempotent',
            )

        usage = UserStickerUsage.objects.get(user=self.user, asset=asset)
        self.assertEqual(usage.use_count, 1)
        UserStickerUsage.objects.all().delete()
        call_command('backfill_sticker_usage', verbosity=0)
        usage = UserStickerUsage.objects.get(user=self.user, asset=asset)
        self.assertEqual(usage.use_count, 1)
        response = self.client.get('/stickers/explore?offset=0&limit=30', **self.authorization())
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['frequent_items'][0]['sticker_asset_id'], asset.id)
