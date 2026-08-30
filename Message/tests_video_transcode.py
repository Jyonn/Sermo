from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import TestCase
from django.utils import timezone

from Message.models import MediaAsset
from Message.video_transcode import delete_expired_original, refresh_video_transcode, submit_video_transcode


class VideoTranscodeTests(TestCase):
    def asset(self, **overrides):
        values = dict(
            source_key='sermo/messages/video/source.mov',
            source_uri='https://media.example/source.mov',
            kind=MediaAsset.KIND_VIDEO,
            status=MediaAsset.STATUS_READY,
        )
        values.update(overrides)
        return MediaAsset.objects.create(**values)

    @patch('Message.video_transcode._client')
    def test_submit_claims_asset_and_records_persistent_id(self, client_factory):
        client = Mock()
        client.execute.return_value = ({'persistentId': 'job-1'}, Mock())
        client_factory.return_value = ('bucket', client)
        asset = submit_video_transcode(self.asset().id)
        self.assertEqual(asset.transcode_status, MediaAsset.TRANSCODE_PENDING)
        self.assertEqual(asset.transcode_persistent_id, 'job-1')
        self.assertEqual(asset.original_key, 'sermo/messages/video/source.mov')
        self.assertTrue(asset.playback_key.endswith('.mp4'))

    @patch.object(MediaAsset, 'refresh', side_effect=lambda asset, geocode=False: asset)
    @patch('Message.video_transcode.avatar_uri_for_key', side_effect=lambda key: f'https://media.example/{key}')
    @patch('Message.video_transcode._client')
    def test_successful_job_switches_stable_source_to_mp4(self, client_factory, _avatar_uri, _refresh):
        client = Mock()
        client.get_status.return_value = ({'code': 0}, Mock())
        client_factory.return_value = ('bucket', client)
        asset = self.asset(
            original_key='sermo/messages/video/source.mov', original_uri='https://media.example/source.mov',
            playback_key='sermo/messages/video-playback/result.mp4',
            transcode_status=MediaAsset.TRANSCODE_PENDING, transcode_persistent_id='job-1',
        )
        refreshed = refresh_video_transcode(asset.id)
        self.assertEqual(refreshed.transcode_status, MediaAsset.TRANSCODE_READY)
        self.assertEqual(refreshed.source_key, 'sermo/messages/video-playback/result.mp4')
        self.assertEqual(refreshed.mime_type, 'video/mp4')

    @patch('Message.video_transcode.delete_message_media_file')
    def test_original_is_deleted_only_after_retention(self, delete_file):
        asset = self.asset(
            source_key='sermo/messages/video-playback/result.mp4',
            original_key='sermo/messages/video/source.mov', original_uri='https://media.example/source.mov',
            transcode_status=MediaAsset.TRANSCODE_READY,
            transcoded_at=timezone.now() - timedelta(days=8),
        )
        self.assertTrue(delete_expired_original(asset.id))
        delete_file.assert_called_once_with('sermo/messages/video/source.mov')
        asset.refresh_from_db()
        self.assertIsNotNone(asset.original_deleted_at)
