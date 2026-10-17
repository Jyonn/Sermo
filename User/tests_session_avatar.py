from unittest.mock import patch

from django.test import TestCase

from Space.models import Space
from User.models import User
from utils import auth


class SessionAvatarTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Avatar Session', slug='avatar-session', email='session@example.com')
        self.user = User.create(space, 'Avatar', verified=True)

    def test_login_and_refresh_include_current_avatar_without_expanding_jwt(self):
        token = auth.get_login_token(self.user)
        self.assertEqual(token['data']['avatar_uri'], self.user.avatar_uri)
        self.assertEqual(token['data']['avatar_cache_key'], self.user._dictify_avatar_cache_key())
        self.assertNotIn('avatar_uri', auth.decrypt(token['auth'], expected_type=auth.Symbols.ACCESS))
        self.user.avatar_uri = User.build_preset_avatar_uri(3)
        self.user.save(update_fields=['avatar_uri'])
        refreshed = auth.refresh_login_token(token['refresh'])
        self.assertEqual(refreshed['data']['avatar_uri'], self.user.avatar_uri)
        self.assertNotEqual(refreshed['data']['avatar_cache_key'], token['data']['avatar_cache_key'])

    def test_custom_avatar_reuses_signed_display_and_missing_avatar_is_empty(self):
        self.user.avatar_type = 'custom'
        self.user.avatar_uri = 'https://cdn.example/sermo/avatar/test.png'
        with patch('User.models.build_avatar_display_uri', return_value='https://cdn.example/avatar.png?token=example'):
            self.assertEqual(auth.get_login_token(self.user)['data']['avatar_uri'], 'https://cdn.example/avatar.png?token=example')
        self.user.avatar_uri = ''
        self.assertEqual(auth.get_login_token(self.user)['data']['avatar_uri'], '')
