import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from Message.models import Message, MessageTypeChoice
from TravelMap.models import MapAccessGrant, MapChatGrant


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

    def test_chat_map_access_message_is_normalized(self):
        normalized = Message.normalize_content(
            MessageTypeChoice.MAP_ACCESS,
            json.dumps({
                'kind': 'map_access',
                'chat_grant': True,
                'message_key': 'travel_map_join',
            }),
        )

        self.assertEqual(
            json.loads(normalized),
            {
                'kind': 'map_access',
                'chat_grant': True,
                'message_key': 'travel_map_join',
            },
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


class ChatMapGrantTests(SimpleTestCase):
    @patch.object(MapChatGrant, 'status', return_value={'authorized_by_me': False, 'shared_members': []})
    def test_maps_requires_current_user_to_share(self, _status):
        with self.assertRaises(Exception):
            MapChatGrant.maps(SimpleNamespace(id=9), SimpleNamespace(id=1))
