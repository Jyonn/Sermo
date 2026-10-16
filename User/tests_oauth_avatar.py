import base64
import hashlib
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from django.test import TestCase

from Config.models import CI
from Space.models import Space
from User.models import User, UserAvatarTypeChoice
from User.oauth import authorize, exchange, registered_redirect_uri, userinfo
from User.oauth_avatar import profile_avatar_url


class OAuthAvatarTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Avatar OAuth', slug='avatar-oauth', email='avatar@example.com')
        self.user = User.create(self.space, 'Avatar', verified=True)

    def token(self, scope='profile'):
        verifier = 'v' * 43
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        redirect = authorize(self.user, client_id='stillfolio', redirect_uri=registered_redirect_uri(), scope=scope,
                             state='s' * 32, code_challenge=challenge, code_challenge_method='S256', response_type='code')
        code = parse_qs(urlparse(redirect).query)['code'][0]
        return exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier=verifier)['access_token']

    def config(self, key):
        return 'avatars.example.test' if key == CI.QINIU_DOMAIN else 'test-signing-key'

    def custom(self, filename='first.png'):
        self.user.avatar_type = UserAvatarTypeChoice.CUSTOM
        self.user.avatar_uri = f'https://avatars.example.test/sermo/avatar/{filename}'
        self.user.save(update_fields=['avatar_type', 'avatar_uri'])

    def test_profile_preset_is_standalone_and_timestamp_unknown(self):
        self.user.avatar_uri = User.build_preset_avatar_uri(1)
        self.user.save(update_fields=['avatar_uri'])
        result = userinfo(self.token())
        self.assertEqual(result['avatar_url'], 'https://sermo.jyonn.space/assets/avatars/v2/01.png')
        self.assertIsNone(result['avatar_updated_at'])
        self.assertEqual(result['nickname'], self.user.name)

    def test_scope_without_profile_does_not_disclose_avatar(self):
        result = userinfo(self.token('vip'))
        self.assertNotIn('avatar_url', result)
        self.assertNotIn('avatar_updated_at', result)

    def test_empty_or_unrecognized_avatar_is_null(self):
        token = self.token()
        for uri in ('', 'https://evil.example/01.png', 'https://sermo.jyonn.space/assets/avatars/v2/avatar-sheet-6x6.png'):
            self.user.avatar_uri = uri
            self.user.save(update_fields=['avatar_uri'])
            self.assertIsNone(userinfo(token)['avatar_url'])

    @patch('utils.qiniu._required_config')
    def test_custom_url_is_stable_and_changes_with_current_avatar(self, config):
        config.side_effect = self.config
        self.custom()
        token = self.token()
        first = userinfo(token)['avatar_url']
        self.assertTrue(first.startswith('https://api.sermo.jyonn.space/users/oauth/avatar/'))
        with patch('utils.qiniu.time.time', return_value=9999999999):
            self.assertEqual(userinfo(token)['avatar_url'], first)
        self.user.avatar_frame_style = 'spider-web'
        self.user.save(update_fields=['avatar_frame_style'])
        self.assertEqual(userinfo(token)['avatar_url'], first)
        self.custom('second.png')
        second = userinfo(token)['avatar_url']
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(urlparse(first).path).status_code, 404)
        self.user.avatar_type = UserAvatarTypeChoice.PRESET
        self.user.avatar_uri = User.build_preset_avatar_uri(2)
        self.user.save(update_fields=['avatar_type', 'avatar_uri'])
        self.assertEqual(userinfo(token)['avatar_url'], self.user.avatar_uri)
        self.assertEqual(self.client.get(urlparse(second).path).status_code, 404)

    @patch('utils.qiniu._required_config')
    def test_custom_image_requires_no_cookie_and_has_safe_headers(self, config):
        config.side_effect = self.config
        self.custom()
        url = userinfo(self.token())['avatar_url']
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM1sAAAAASUVORK5CYII=')
        upstream = Mock(status_code=200)
        upstream.iter_content.return_value = [png]
        upstream.__enter__ = Mock(return_value=upstream)
        upstream.__exit__ = Mock(return_value=False)
        with patch('User.oauth_avatar.requests.get', return_value=upstream) as fetch:
            response = self.client.get(urlparse(url).path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, png)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['Cross-Origin-Resource-Policy'], 'cross-origin')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['Cache-Control'], 'public, max-age=300')
        self.assertIn('/format/png', fetch.call_args.args[0])
        self.assertFalse(fetch.call_args.kwargs['allow_redirects'])

    @patch('utils.qiniu._required_config')
    def test_custom_cannot_reference_other_uploads_or_hosts(self, config):
        config.side_effect = self.config
        self.custom()
        for uri in ('https://avatars.example.test/sermo/message/private.png', 'https://evil.example/sermo/avatar/test.png'):
            self.user.avatar_uri = uri
            self.assertIsNone(profile_avatar_url(self.user))
        with patch('User.oauth_avatar.requests.get') as fetch:
            response = self.client.get('/users/oauth/avatar/forged-token')
        self.assertEqual(response.status_code, 404)
        fetch.assert_not_called()

    @patch('utils.qiniu._required_config')
    def test_missing_storage_configuration_returns_null(self, config):
        from User.validators import UserErrors
        config.side_effect = UserErrors.AVATAR_STORAGE_NOT_CONFIGURED
        self.custom()
        self.assertIsNone(userinfo(self.token())['avatar_url'])

    @patch('utils.qiniu._required_config')
    def test_proxy_rejects_redirects_non_images_and_oversize(self, config):
        from utils.qiniu import AVATAR_MAX_FILE_SIZE
        config.side_effect = self.config
        self.custom()
        path = urlparse(userinfo(self.token())['avatar_url']).path
        for status, chunks in ((302, []), (200, [b'<html>not an image</html>']), (200, [b'x' * (AVATAR_MAX_FILE_SIZE + 1)])):
            upstream = Mock(status_code=status)
            upstream.iter_content.return_value = chunks
            upstream.__enter__ = Mock(return_value=upstream)
            upstream.__exit__ = Mock(return_value=False)
            with self.subTest(status=status, size=sum(map(len, chunks))), patch('User.oauth_avatar.requests.get', return_value=upstream):
                response = self.client.get(path)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response['Cache-Control'], 'no-store')
