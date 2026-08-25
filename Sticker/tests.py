from unittest.mock import patch
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from Sticker.models import StickerAsset, UserSticker
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
