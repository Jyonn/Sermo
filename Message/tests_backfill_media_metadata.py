from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from Message.models import MediaAsset


class BackfillMediaMetadataCommandTests(TestCase):
    def test_geocodes_complete_asset_without_refetching_qiniu_metadata(self):
        asset = MediaAsset.objects.create(
            source_key='sermo/messages/image/complete.jpg',
            source_uri='https://example.test/sermo/messages/image/complete.jpg',
            kind=MediaAsset.KIND_IMAGE,
            status=MediaAsset.STATUS_READY,
            file_size=2048,
            pixel_width=800,
            pixel_height=600,
            latitude=31.2304,
            longitude=121.4737,
            address='',
        )

        with patch.object(MediaAsset, 'refresh') as refresh, patch.object(
            MediaAsset, 'refresh_geocoding', return_value=asset,
        ) as geocode:
            call_command('backfill_media_metadata', stdout=StringIO())

        refresh.assert_not_called()
        geocode.assert_called_once_with(asset)

    def test_refreshes_incomplete_image_and_video_assets(self):
        image = MediaAsset.objects.create(
            source_key='sermo/messages/image/incomplete.jpg',
            source_uri='https://example.test/sermo/messages/image/incomplete.jpg',
            kind=MediaAsset.KIND_IMAGE,
        )
        video = MediaAsset.objects.create(
            source_key='sermo/statements/video/incomplete.mp4',
            source_uri='https://example.test/sermo/statements/video/incomplete.mp4',
            kind=MediaAsset.KIND_VIDEO,
        )

        with patch.object(MediaAsset, 'refresh', side_effect=lambda asset, geocode=True: asset) as refresh:
            call_command('backfill_media_metadata', stdout=StringIO())

        self.assertEqual([call.args[0].id for call in refresh.call_args_list], [image.id, video.id])
