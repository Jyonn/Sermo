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
            pixel_width=1920, pixel_height=1080,
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

    @patch('Message.video_transcode.fetch_qiniu_avinfo', return_value={})
    @patch('Message.video_transcode.parse_avinfo', return_value={'file_size':11_500_000,'duration_seconds':60,'pixel_width':1280,'pixel_height':720})
    @patch.object(MediaAsset, 'refresh', side_effect=lambda asset, geocode=False: asset)
    @patch('Message.video_transcode.avatar_uri_for_key', side_effect=lambda key: f'https://media.example/{key}')
    @patch('Message.video_transcode._client')
    def test_successful_job_switches_stable_source_to_mp4(self, client_factory, _avatar_uri, _refresh, _parse, _fetch):
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
        self.assertEqual(refreshed.file_size, 11_500_000)

    @patch('Message.video_transcode.fetch_qiniu_avinfo', return_value={})
    @patch('Message.video_transcode.parse_avinfo', return_value={'file_size':21_000_000,'duration_seconds':60})
    @patch('Message.video_transcode.avatar_uri_for_key', return_value='https://media.example/result.mp4')
    @patch('Message.video_transcode._client')
    def test_oversized_output_never_replaces_the_source(self, factory, *_):
        client=Mock();client.get_status.return_value=({'code':0},Mock());factory.return_value=('bucket',client)
        asset=self.asset(transcode_status=MediaAsset.TRANSCODE_PENDING,playback_key='result.mp4')
        result=refresh_video_transcode(asset.id)
        self.assertEqual(result.transcode_status,MediaAsset.TRANSCODE_FAILED)
        self.assertEqual(result.source_key,asset.source_key)

    @patch('Message.video_transcode._client')
    def test_portrait_encoding_and_retry_limit(self, factory):
        client=Mock();client.execute.return_value=({'persistentId':'job'},Mock());factory.return_value=('bucket',client)
        asset=self.asset(pixel_width=1080,pixel_height=1920,file_size=90_000_000)
        result=submit_video_transcode(asset.id)
        self.assertIn('/s/720x1280>/',client.execute.call_args.args[1][0])
        self.assertEqual(result.original_file_size,90_000_000)
        self.assertEqual(result.transcode_attempts,1)
        MediaAsset.objects.filter(id=asset.id).update(transcode_status=MediaAsset.TRANSCODE_FAILED,transcode_error='playback exceeds 20 MB per minute')
        retry=submit_video_transcode(asset.id)
        self.assertNotEqual(retry.playback_key,result.playback_key)
        self.assertIn('/vb/1000k/',client.execute.call_args.args[1][0])
        MediaAsset.objects.filter(id=asset.id).update(transcode_status=MediaAsset.TRANSCODE_FAILED,transcode_attempts=3)
        self.assertIsNone(submit_video_transcode(asset.id))
        self.assertEqual(client.execute.call_count,2)

    @patch('Message.video_transcode._client', side_effect=OSError('temporary outage'))
    def test_poll_network_error_keeps_existing_job_pending(self, _):
        asset=self.asset(transcode_status=MediaAsset.TRANSCODE_PENDING)
        self.assertEqual(refresh_video_transcode(asset.id).transcode_status,MediaAsset.TRANSCODE_PENDING)

    @patch.object(MediaAsset, 'queue_video_transcode')
    def test_reused_unprocessed_video_is_scheduled(self, queue):
        asset=self.asset(content_hash='a'*64,file_size=100)
        with self.captureOnCommitCallbacks(execute=True):
            result=MediaAsset.queue(asset.source_key,asset.source_uri,asset.kind,content_hash=asset.content_hash,file_size=100)
        self.assertEqual(result.id,asset.id);queue.assert_called_once_with(asset.id)

    def test_processed_duplicate_matches_original_upload_size(self):
        asset=self.asset(content_hash='b'*64,file_size=10_000_000,original_file_size=90_000_000)
        self.assertEqual(MediaAsset.find_duplicate('b'*64,90_000_000).id,asset.id)
        MediaAsset.objects.filter(pk=asset.pk).update(original_file_size=None,transcode_status=MediaAsset.TRANSCODE_READY)
        self.assertEqual(MediaAsset.find_duplicate('b'*64,90_000_000).id,asset.id)

    @patch('Message.image_metadata.fetch_qiniu_image_info', return_value={})
    @patch('Message.image_metadata.parse_image_info', return_value={'file_size':100,'pixel_width':30,'pixel_height':20})
    @patch('Message.image_metadata.fetch_qiniu_exif', return_value={})
    @patch('Message.image_metadata.parse_exif', return_value={})
    def test_metadata_fetch_cannot_reset_a_concurrent_transcode_job(self, *_):
        stale=self.asset(kind=MediaAsset.KIND_IMAGE)
        MediaAsset.objects.filter(pk=stale.pk).update(transcode_status=MediaAsset.TRANSCODE_PENDING,transcode_persistent_id='job')
        MediaAsset.refresh(stale,geocode=False)
        stale.refresh_from_db()
        self.assertEqual(stale.transcode_status,MediaAsset.TRANSCODE_PENDING)
        self.assertEqual(stale.transcode_persistent_id,'job')

    @patch('Message.image_metadata.fetch_qiniu_image_info', return_value={})
    @patch('Message.image_metadata.parse_image_info', return_value={'file_size':100,'pixel_width':30,'pixel_height':20})
    @patch('Message.image_metadata.fetch_qiniu_exif', return_value={})
    @patch('Message.image_metadata.parse_exif', return_value={})
    def test_stale_metadata_cannot_overwrite_new_playback_dimensions(self, *_):
        stale=self.asset(kind=MediaAsset.KIND_IMAGE)
        MediaAsset.objects.filter(pk=stale.pk).update(source_key='new',source_uri='https://media.example/new',file_size=50,pixel_width=15)
        MediaAsset.refresh(stale,geocode=False)
        stale.refresh_from_db();self.assertEqual(stale.file_size,50);self.assertEqual(stale.pixel_width,15)

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
