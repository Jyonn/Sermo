import base64
import json
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase

from Message.image_metadata import _reverse_geocode_opencage, parse_image_info, reverse_geocode, search_nearby_places
from Message.video_metadata import parse_avinfo
from Message.models import MediaAsset, Message, MessageTypeChoice
from utils.qiniu import QINIU_UPLOAD_URL, build_message_media_key, build_upload_token, validate_message_media_key, validate_message_media_size
from utils.global_settings import Globals


class MessageFileUploadTests(SimpleTestCase):
    def test_uploads_use_the_east_china_bucket_endpoint(self):
        self.assertEqual(QINIU_UPLOAD_URL, 'https://up-z0.qiniup.com')

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

    def test_empty_audio_container_is_rejected(self):
        with self.assertRaises(Exception):
            validate_message_media_size('audio', 5)

    @patch('utils.qiniu._required_config', side_effect=['access-key', 'secret-key', 'bucket'])
    def test_audio_upload_policy_has_minimum_size(self, _required_config):
        token = build_upload_token('sermo/messages/audio/test.webm', min_file_size=1024)
        encoded_policy = token.rsplit(':', 1)[-1]
        padding = '=' * (-len(encoded_policy) % 4)
        policy = json.loads(base64.urlsafe_b64decode(encoded_policy + padding))
        self.assertEqual(policy['fsizeMin'], 1024)


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
    def test_nearby_places_use_distance_sorting_and_normalize_coordinates(self, get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {'status': '1', 'pois': [{
            'id': 'B001', 'name': '西湖文化广场', 'location': '120.165000,30.280000',
            'distance': '286', 'type': '风景名胜;公园广场', 'pname': '浙江省',
            'cityname': '杭州市', 'adname': '拱墅区', 'address': '环城北路',
            'business': {'business_area': '武林商圈'},
        }]}
        get.return_value = response
        with (
            patch.object(Globals, 'AMAP_WEBSERVICE_KEY', 'test-key', create=True),
            patch.object(Globals, 'AMAP_PLACE_AROUND_URL', 'https://restapi.amap.com/v5/place/around', create=True),
        ):
            places = search_nearby_places(30.27, 120.16, keyword='西湖', radius=3000)
        self.assertEqual(places[0]['name'], '西湖文化广场')
        self.assertEqual(places[0]['latitude'], 30.28)
        self.assertEqual(get.call_args.kwargs['params']['sortrule'], 'distance')
        self.assertEqual(get.call_args.kwargs['params']['keywords'], '西湖')

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

    @patch('Message.image_metadata.reverse_geocode')
    def test_selected_place_address_is_preserved(self, geocode):
        normalized = Message.normalize_content(MessageTypeChoice.LOCATION, json.dumps({
            'latitude': 30.28, 'longitude': 120.165,
            'address': '西湖文化广场', 'geocoding_provider': 'amap',
            'obscure': True,
        }))
        self.assertEqual(json.loads(normalized)['address'], '西湖文化广场')
        self.assertNotIn('obscured', json.loads(normalized))
        geocode.assert_not_called()
