import gzip
import json

from django.test import TestCase, override_settings
from Message.test_compact import decode
from Space.models import Space
from User.models import User
from utils import auth


@override_settings(MIDDLEWARE=['utils.middleware.ChatListCompression', 'utils.middleware.APIPacker'])
class UserMeTransportTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Me transport', slug='me-transport', email='me@example.test')
        self.user = User.create(space, 'Me', verified=True)
        self.headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.user)['auth']}"}

    def read(self, query='', zipped=False):
        response = self.client.get('/users/me' + query, **self.headers,
                                   **({'HTTP_ACCEPT_ENCODING': 'gzip'} if zipped else {}))
        content = gzip.decompress(response.content) if response.get('Content-Encoding') == 'gzip' else response.content
        return response, json.loads(content)['body']

    def test_full_profile_is_lossless_for_legacy_compact_and_gzip_clients(self):
        normal, legacy = self.read()
        self.assertIn('resource_inventory', legacy)
        self.assertIn('capabilities', legacy['growth'])
        self.assertNotIn('format', legacy)
        for query in ('', '?format=compact-v1'):
            for zipped in (False, True):
                response, body = self.read(query, zipped)
                self.assertEqual(decode(body), legacy)
                self.assertIn('no-store', response['Cache-Control'])
                if zipped:
                    self.assertEqual(response.get('Content-Encoding'), 'gzip')
                    self.assertIn('Accept-Encoding', response['Vary'])
                    self.assertLess(len(response.content), len(normal.content) * .4)
                    print(f'User me {query or "legacy"}: {len(normal.content)} -> {len(response.content)} bytes')

    def test_unauthenticated_reads_do_not_expose_profile(self):
        for query in ('', '?format=compact-v1'):
            response = self.client.get('/users/me' + query)
            self.assertIsNone(response.json()['body'])
            self.assertIn('no-store', response['Cache-Control'])
