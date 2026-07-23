from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from Message.image_metadata import parse_image_info, reverse_geocode
from utils.global_settings import Globals


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

    @patch('Message.image_metadata._reverse_geocode_nominatim')
    @patch('Message.image_metadata._reverse_geocode_amap')
    def test_reverse_geocode_falls_back_to_nominatim(self, amap, nominatim):
        amap.side_effect = RuntimeError('temporary failure')
        nominatim.return_value = '杭州市临平区'

        with patch.object(Globals, 'AMAP_WEBSERVICE_KEY', 'test-key', create=True):
            address, provider = reverse_geocode(30.4, 120.3)

        self.assertEqual(address, '杭州市临平区')
        self.assertEqual(provider, 'nominatim')
