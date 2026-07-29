import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from Message.models import Message, MessageTypeChoice
from TravelMap.geocoding import reverse_geocode_check_in
from TravelMap.models import MapAccessGrant, MapChatGrant
from utils.global_settings import Globals


class TravelMapMessageTests(SimpleTestCase):
    def test_map_access_message_is_normalized(self):
        normalized = Message.normalize_content(
            MessageTypeChoice.MAP_ACCESS,
            json.dumps({'kind': 'map_access', 'target_user_id': 42}),
        )

        self.assertEqual(
            json.loads(normalized),
            {'kind': 'map_access', 'target_user_id': 42},
        )


class MapAccessGrantTests(SimpleTestCase):
    @patch.object(MapAccessGrant, 'grant')
    @patch.object(MapAccessGrant, 'has_access', return_value=True)
    def test_reciprocate_requires_existing_incoming_grant(self, has_access, grant):
        current = SimpleNamespace(id=1, space_id=7)
        owner = SimpleNamespace(id=2, space_id=7)

        MapAccessGrant.reciprocate(current, owner)

        has_access.assert_called_once_with(owner, current)
        grant.assert_called_once_with(current, owner)

    def test_cross_space_pair_is_rejected(self):
        current = SimpleNamespace(id=1, space_id=7)
        other = SimpleNamespace(id=2, space_id=8)

        with self.assertRaises(Exception):
            MapAccessGrant._validate_pair(current, other)


class CheckInGeocodingTests(SimpleTestCase):
    @patch('TravelMap.geocoding.requests.get')
    def test_opencage_subdivision_is_used_as_stable_region_code(self, get):
        get.return_value.json.return_value = {
            'results': [{
                'components': {
                    'ISO_3166-1_alpha-3': 'CHN',
                    'ISO_3166-2': 'CN-ZJ',
                    'country': '中国',
                    'state': '浙江省',
                },
            }],
        }
        with patch.object(Globals, 'OPENCAGE_API_KEY', 'key', create=True), patch.object(
            Globals,
            'OPENCAGE_GEOCODING_URL',
            'https://example.test/reverse',
            create=True,
        ):
            result = reverse_geocode_check_in(30.2, 120.1)

        self.assertEqual(result['region_code'], 'CHN:CN-ZJ')
        self.assertEqual(result['region_name'], '浙江省')


class ChatMapGrantTests(SimpleTestCase):
    @patch.object(MapChatGrant, 'status', return_value={'authorized_by_me': False, 'shared_members': []})
    def test_maps_requires_current_user_to_share(self, _status):
        with self.assertRaises(Exception):
            MapChatGrant.maps(SimpleNamespace(id=9), SimpleNamespace(id=1))
