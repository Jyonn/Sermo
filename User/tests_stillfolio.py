import json
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase
from django.utils import timezone

from Config.models import Config, CI
from Space.models import Space
from User.models import StillfolioCredential, User
from User.stillfolio import CLIENT_ID, binding, config_cipher
from utils import auth


class StillfolioBindingTests(TestCase):
    album = '6269bb3c-29ef-423a-8197-60bd89b1e217'

    def setUp(self):
        cipher = config_cipher()
        Config.update_value(CI.STILLFOLIO_CLIENT_ID, CLIENT_ID)
        Config.update_value(CI.STILLFOLIO_CLIENT_SECRET, cipher.encrypt(b'test-secret').decode())
        Config.update_value(CI.STILLFOLIO_STORAGE_KEY, cipher.encrypt(Fernet.generate_key()).decode())
        self.first = self.account('studio', '13812345678')
        self.second = self.account('archive', '+8613812345678')

    @staticmethod
    def account(slug, phone):
        space = Space.objects.create(slug=slug, name=slug, email=f'{slug}@example.test')
        user = User.create(space, slug, verified=True)
        user.phone = phone
        user.phone_verified_at = timezone.now()
        user.save(update_fields=['phone', 'phone_verified_at'])
        return user

    @staticmethod
    def headers(user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user)['auth']}

    def begin(self, user):
        response = self.client.post('/users/me/stillfolio', data=json.dumps({
            'action': 'connect', 'origin': 'https://frienden.6-79.cn', 'path': f'/{user.space.slug}/app/chats/42',
        }), content_type='application/json', **self.headers(user))
        self.assertEqual(response.status_code, 200)
        url = urlsplit(response.json()['authorize_url'])
        self.assertEqual(url.netloc, 'api.stillfolio.jyonn.space')
        query = parse_qs(url.query)
        self.assertEqual(query['redirect_uri'], [binding.callback])
        self.assertEqual(query['scope'], ['albums.read shares.create'])
        return query['state'][0]

    def provider(self, path, **kwargs):
        if path == 'token/':
            return {'access_token': 'album-access', 'refresh_token': 'album-refresh', 'expires_in': 900,
                    'scope': 'albums.read shares.create'}
        if path == 'frienden-account/':
            self.assertEqual(kwargs['data'], {'token': 'album-access', 'phone': '+8613812345678'})
            return {'sub': f'stillfolio:{self.album}'}
        if path == 'albums/':
            self.assertEqual(kwargs, {'access': 'album-access', 'params': {'offset': '100'}})
            return {'albums': [{'id': self.album, 'name': '云端相册'}], 'next_offset': None}
        self.assertEqual(path, 'shares/')
        self.assertEqual(kwargs['data'], {'album_id': self.album})
        self.assertEqual(kwargs['idempotency_key'], f'{self.first.pk}:{self.album}')
        return {'url': f'https://stillfolio.jyonn.space/share/{self.album}'}

    def test_callback_and_shared_phone_credentials(self):
        with patch.object(binding, 'remote', side_effect=self.provider):
            state = self.begin(self.first)
            response = self.client.get('/users/stillfolio/callback', {'state': state, 'code': 'code'})
            self.assertEqual(response['Location'], 'https://frienden.6-79.cn/studio/app/chats/42?stillfolio=connected')
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertEqual(self.client.get('/users/stillfolio/callback', {'state': state, 'code': 'code'}).status_code, 409)
            self.assertEqual(binding.status(self.second), 'connect_required')
            self.client.get('/users/stillfolio/callback', {'state': self.begin(self.second), 'code': 'second'})
            self.assertEqual(binding.status(self.second), 'connected')
            self.assertEqual(StillfolioCredential.objects.count(), 1)

    def test_only_connected_account_can_list_and_share_albums(self):
        with patch.object(binding, 'remote', side_effect=self.provider) as remote:
            response = self.client.get('/users/me/stillfolio/albums', {'offset': 100}, **self.headers(self.second))
            self.assertEqual(response.status_code, 409)
            remote.assert_not_called()
            self.client.get('/users/stillfolio/callback', {'state': self.begin(self.first), 'code': 'code'})
            response = self.client.get('/users/me/stillfolio/albums', {'offset': 100}, **self.headers(self.first))
            self.assertEqual(response.json()['albums'][0]['id'], self.album)
            response = self.client.post('/users/me/stillfolio/shares', data=json.dumps({
                'album_id': self.album, 'operation_id': self.album,
            }), content_type='application/json', **self.headers(self.first))
            self.assertEqual(response.json()['url'], f'https://stillfolio.jyonn.space/share/{self.album}')
