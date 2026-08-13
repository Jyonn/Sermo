from unittest.mock import patch

from django.test import TestCase

from Sticker.models import StickerAsset


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
