import json
import math
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from Message.image_metadata import _reverse_geocode_opencage, parse_image_info, reverse_geocode
from Message.video_metadata import parse_avinfo
from Message.models import MediaAsset, MediaAssetAlias, Message, MessageTypeChoice, random_point_within_radius
from utils.qiniu import build_message_media_key, validate_message_media_key
from utils.global_settings import Globals


class MessageFileUploadTests(SimpleTestCase):
    def test_arbitrary_file_extension_is_preserved(self):
        key = build_message_media_key('file', 'scene.blend1', 'application/octet-stream')

        self.assertTrue(key.endswith('.blend1'))
        self.assertEqual(validate_message_media_key('file', key), key)

    def test_unsafe_or_missing_extension_uses_bin(self):
        self.assertTrue(build_message_media_key('file', 'README', '').endswith('.bin'))
        self.assertTrue(build_message_media_key('file', 'archive.超长格式', '').endswith('.bin'))

    def test_file_key_still_rejects_forged_paths(self):
        with self.assertRaises(Exception):
            validate_message_media_key('file', 'sermo/messages/file/../image/unsafe.exe')


class ImageMetadataTests(SimpleTestCase):
    def test_parse_image_info(self):
        self.assertEqual(
            parse_image_info({'size': 214513, 'width': 640, 'height': 427}),
            {
                'file_size': 214513,
                'pixel_width': 640,
                'pixel_height': 427,
            },
        )

    @patch('Message.image_metadata.requests.get')
    def test_reverse_geocode_prefers_amap(self, get):
        response = Mock()
        response.json.return_value = {
            'status': '1',
            'regeocode': {'formatted_address': '浙江省杭州市临平区'},
        }
        response.raise_for_status.return_value = None
        get.return_value = response

        with (
            patch.object(Globals, 'AMAP_WEBSERVICE_KEY', 'test-key', create=True),
            patch.object(
                Globals,
                'AMAP_REVERSE_GEOCODING_URL',
                'https://restapi.amap.com/v3/geocode/regeo',
                create=True,
            ),
        ):
            address, provider = reverse_geocode(30.4, 120.3)

        self.assertEqual(address, '浙江省杭州市临平区')
        self.assertEqual(provider, 'amap')
        self.assertEqual(get.call_args.kwargs['params']['location'], '120.3,30.4')

    @patch('Message.image_metadata.requests.get')
    def test_opencage_reverse_geocoding(self, get):
        response = Mock()
        response.json.return_value = {
            'results': [{'formatted': '新加坡滨海湾'}],
        }
        response.raise_for_status.return_value = None
        get.return_value = response

        with (
            patch.object(Globals, 'OPENCAGE_API_KEY', 'test-key', create=True),
            patch.object(
                Globals,
                'OPENCAGE_GEOCODING_URL',
                'https://api.opencagedata.com/geocode/v1/json',
                create=True,
            ),
        ):
            address = _reverse_geocode_opencage(1.2834, 103.8607)

        self.assertEqual(address, '新加坡滨海湾')
        self.assertEqual(get.call_args.kwargs['params']['q'], '1.2834,103.8607')
        self.assertEqual(get.call_args.kwargs['params']['language'], 'zh-CN')

    @patch('Message.image_metadata._reverse_geocode_nominatim')
    @patch('Message.image_metadata._reverse_geocode_opencage')
    @patch('Message.image_metadata._reverse_geocode_amap')
    def test_reverse_geocode_uses_opencage_after_amap(self, amap, opencage, nominatim):
        amap.side_effect = RuntimeError('temporary failure')
        opencage.return_value = 'Singapore'
        nominatim.return_value = '杭州市临平区'

        with (
            patch.object(Globals, 'AMAP_WEBSERVICE_KEY', 'amap-key', create=True),
            patch.object(Globals, 'OPENCAGE_API_KEY', 'opencage-key', create=True),
        ):
            address, provider = reverse_geocode(30.4, 120.3)

        self.assertEqual(address, 'Singapore')
        self.assertEqual(provider, 'opencage')
        nominatim.assert_not_called()

    @patch('Message.image_metadata._reverse_geocode_nominatim')
    @patch('Message.image_metadata._reverse_geocode_opencage')
    @patch('Message.image_metadata._reverse_geocode_amap')
    def test_reverse_geocode_falls_back_to_nominatim(self, amap, opencage, nominatim):
        amap.side_effect = RuntimeError('Amap unavailable')
        opencage.side_effect = RuntimeError('OpenCage unavailable')
        nominatim.return_value = '杭州市临平区'

        with (
            patch.object(Globals, 'AMAP_WEBSERVICE_KEY', 'amap-key', create=True),
            patch.object(Globals, 'OPENCAGE_API_KEY', 'opencage-key', create=True),
        ):
            address, provider = reverse_geocode(30.4, 120.3)

        self.assertEqual(address, '杭州市临平区')
        self.assertEqual(provider, 'nominatim')


class VideoMetadataTests(SimpleTestCase):
    def test_parse_avinfo_extracts_video_and_quicktime_metadata(self):
        metadata = parse_avinfo({
            'streams': [
                {
                    'codec_type': 'video',
                    'codec_name': 'h264',
                    'width': 1920,
                    'height': 1080,
                    'avg_frame_rate': '30000/1001',
                    'tags': {
                        'com.apple.quicktime.make': 'Apple',
                        'com.apple.quicktime.model': 'iPhone',
                        'com.apple.quicktime.location.ISO6709': '+31.2304+121.4737/',
                    },
                },
                {'codec_type': 'audio', 'codec_name': 'aac'},
            ],
            'format': {
                'duration': '12.5',
                'size': '3145728',
                'bit_rate': '2048000',
                'tags': {'creation_time': '2026-07-24T10:20:30Z'},
            },
        })

        self.assertEqual(metadata['pixel_width'], 1920)
        self.assertEqual(metadata['pixel_height'], 1080)
        self.assertAlmostEqual(metadata['frame_rate'], 29.97002997)
        self.assertEqual(metadata['video_codec'], 'h264')
        self.assertEqual(metadata['audio_codec'], 'aac')
        self.assertEqual(metadata['make'], 'Apple')
        self.assertEqual(metadata['model'], 'iPhone')
        self.assertEqual(metadata['latitude'], 31.2304)
        self.assertEqual(metadata['longitude'], 121.4737)


class UnifiedMediaAssetTests(TestCase):
    def test_image_exif_time_is_interpreted_as_beijing_wall_time(self):
        from Message.image_metadata import parse_exif

        metadata = parse_exif({'DateTimeOriginal': {'val': '2026:08:26 10:00:00'}})

        self.assertEqual(metadata['taken_at'].isoformat(), '2026-08-26T10:00:00+08:00')

    @patch('Message.image_metadata.reverse_geocode', return_value=('上海市', 'opencage'))
    @patch('Message.image_metadata.fetch_qiniu_exif')
    @patch('Message.image_metadata.fetch_qiniu_image_info')
    def test_image_and_video_use_the_same_metadata_model(self, image_info, exif, geocode):
        image_info.return_value = {'size': 1024, 'width': 640, 'height': 480}
        exif.return_value = {}
        image = MediaAsset.objects.create(
            source_key='sermo/messages/image/shared.jpg',
            source_uri='https://resource.example.com/sermo/messages/image/shared.jpg',
            kind=MediaAsset.KIND_IMAGE,
        )
        MediaAsset.refresh(image)

        self.assertEqual(image.status, MediaAsset.STATUS_READY)
        self.assertEqual(image.pixel_width, 640)
        self.assertEqual(image.jsonl()['file_size'], 1024)

        video = MediaAsset.objects.create(
            source_key='sermo/messages/video/shared.mp4',
            source_uri='https://resource.example.com/sermo/messages/video/shared.mp4',
            kind=MediaAsset.KIND_VIDEO,
        )
        with patch('Message.video_metadata.fetch_qiniu_avinfo', return_value={
            'streams': [{'codec_type': 'video', 'codec_name': 'h264', 'width': 1280, 'height': 720}],
            'format': {'duration': '8.5', 'size': '2048'},
        }):
            MediaAsset.refresh(video)

        self.assertEqual(video.status, MediaAsset.STATUS_READY)
        self.assertEqual(video.pixel_width, 1280)
        self.assertEqual(video.duration_seconds, 8.5)
        self.assertEqual(MediaAsset.objects.count(), 2)

    def test_queue_reuses_metadata_for_the_same_storage_key(self):
        first = MediaAsset.queue(
            'sermo/messages/image/shared.jpg',
            'https://resource.example.com/sermo/messages/image/shared.jpg?token=old',
            MediaAsset.KIND_IMAGE,
        )
        second = MediaAsset.queue(
            'sermo/messages/image/shared.jpg',
            'https://resource.example.com/sermo/messages/image/shared.jpg?token=new',
            MediaAsset.KIND_IMAGE,
        )

        self.assertEqual(first.id, second.id)
        self.assertEqual(MediaAsset.objects.count(), 1)
        second.refresh_from_db()
        self.assertIn('token=new', second.source_uri)

    def test_audio_and_file_are_assets_without_metadata_fetch(self):
        with patch.object(MediaAsset, 'fetch_async') as fetch:
            audio = MediaAsset.queue(
                'sermo/messages/audio/voice.m4a', 'https://resource.example.com/sermo/messages/audio/voice.m4a',
                MediaAsset.KIND_AUDIO, mime_type='audio/mp4', duration_seconds=12,
            )
            file = MediaAsset.queue(
                'sermo/messages/file/report.pdf', 'https://resource.example.com/sermo/messages/file/report.pdf',
                MediaAsset.KIND_FILE, mime_type='application/pdf', file_size=2048,
            )

        self.assertEqual(audio.status, MediaAsset.STATUS_READY)
        self.assertEqual(audio.duration_seconds, 12)
        self.assertEqual(file.file_size, 2048)
        fetch.assert_not_called()

    def test_legacy_blob_slug_resolves_to_asset(self):
        asset = MediaAsset.objects.create(
            source_key='sermo/messages/image/legacy.jpg',
            source_uri='https://resource.example.com/sermo/messages/image/legacy.jpg',
            kind=MediaAsset.KIND_IMAGE,
        )
        MediaAssetAlias.objects.create(slug='legacy-message-slug', asset=asset)

        self.assertEqual(MediaAssetAlias.resolve(asset.blob_slug), asset)
        self.assertEqual(MediaAssetAlias.resolve('legacy-message-slug'), asset)


class LocationMessageTests(SimpleTestCase):
    @patch('Message.image_metadata.reverse_geocode', return_value=('新加坡滨海湾', 'opencage'))
    def test_normalize_location_message(self, geocode):
        normalized = Message.normalize_content(
            MessageTypeChoice.LOCATION,
            json.dumps({'latitude': 1.2834012, 'longitude': 103.8607123}),
        )

        self.assertEqual(
            json.loads(normalized),
            {
                'kind': 'location',
                'latitude': 1.283401,
                'longitude': 103.860712,
                'address': '新加坡滨海湾',
                'geocoding_provider': 'opencage',
            },
        )
        geocode.assert_called_once_with(1.283401, 103.860712)

    @patch('Message.models.random_point_within_radius', return_value=(1.4, 103.9))
    @patch('Message.image_metadata.reverse_geocode', return_value=('模糊位置', 'opencage'))
    def test_normalize_obscured_location_uses_randomized_coordinates(self, geocode, randomize):
        normalized = Message.normalize_content(
            MessageTypeChoice.LOCATION,
            json.dumps({
                'latitude': 1.2834012,
                'longitude': 103.8607123,
                'obscure': True,
            }),
        )

        self.assertEqual(
            json.loads(normalized),
            {
                'kind': 'location',
                'latitude': 1.4,
                'longitude': 103.9,
                'address': '模糊位置',
                'geocoding_provider': 'opencage',
                'obscured': True,
                'obscure_radius_km': 50,
            },
        )
        randomize.assert_called_once_with(1.283401, 103.860712)
        geocode.assert_called_once_with(1.4, 103.9)

    def test_randomized_location_stays_within_fifty_kilometers(self):
        class FixedRandom:
            values = iter((1.0, 0.25))

            def random(self):
                return next(self.values)

        latitude, longitude = random_point_within_radius(
            30.2741,
            120.1551,
            rng=FixedRandom(),
        )

        latitude_delta = math.radians(latitude - 30.2741)
        longitude_delta = math.radians(longitude - 120.1551)
        original_latitude = math.radians(30.2741)
        randomized_latitude = math.radians(latitude)
        haversine = (
            math.sin(latitude_delta / 2) ** 2
            + math.cos(original_latitude)
            * math.cos(randomized_latitude)
            * math.sin(longitude_delta / 2) ** 2
        )
        distance = 2 * 6371.0088 * math.asin(math.sqrt(haversine))
        self.assertLessEqual(distance, 50.001)
