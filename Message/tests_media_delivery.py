from unittest.mock import patch
from django.test import SimpleTestCase
from Message.models import MediaAsset
from utils.qiniu import build_message_image_display_uri
from Message.media_delivery import media_delivery_response


class MediaDeliveryTests(SimpleTestCase):
    def test_pending_video_download_cannot_fall_back_to_original(self):
        asset=MediaAsset(kind=MediaAsset.KIND_VIDEO,source_uri='original',transcode_status=MediaAsset.TRANSCODE_PENDING)
        response=media_delivery_response(asset,download=True)
        self.assertEqual(response.status_code,409)
        self.assertEqual(response['Cache-Control'],'no-store')

    @patch('Message.models.sign_private_download_url', side_effect=lambda uri:uri)
    def test_ready_download_uses_processed_video(self, _):
        asset=MediaAsset(kind=MediaAsset.KIND_VIDEO,source_uri='https://media.example/processed.mp4',original_uri='https://media.example/original.mov',transcode_status=MediaAsset.TRANSCODE_READY)
        self.assertEqual(media_delivery_response(asset,download=True)['Location'],asset.source_uri)
    @patch('utils.qiniu.sign_private_processed_url', side_effect=lambda uri, operation: (uri, operation))
    def test_photo_display_is_bounded_webp(self, _):
        uri, operation=build_message_image_display_uri('https://media.example/image.png','image/png')
        self.assertIn('/w/1920/h/1920/',operation);self.assertIn('/format/webp',operation)

    @patch('utils.qiniu.sign_private_download_url', return_value='animation')
    def test_animated_formats_are_preserved(self, _):
        self.assertEqual(build_message_image_display_uri('gif','image/gif'),'animation')
        self.assertEqual(build_message_image_display_uri('webp','image/webp'),'animation')

    def test_display_cache_identity_is_separate_from_original_and_thumbnail(self):
        asset=MediaAsset(kind=MediaAsset.KIND_IMAGE)
        self.assertEqual(asset.delivery_path('/messages/blob/id'),'/messages/blob/id/display')
        self.assertEqual(asset.delivery_path('/messages/blob/id/thumbnail',thumbnail=True),'/messages/blob/id/thumbnail')
        asset.kind=MediaAsset.KIND_VIDEO;asset.transcode_status=MediaAsset.TRANSCODE_READY
        self.assertEqual(asset.delivery_path('/messages/blob/id'),'/messages/blob/id?variant=playback')
