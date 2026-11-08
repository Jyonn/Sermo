from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from User.models import PockloConnection, PockloCredential, User
from User.pocklo import PockloError, access_for, consume, finish, start, status
from Message.providers.pocklo import download_target, preview, share_token


class PockloConnectionTests(TestCase):
    def setUp(self):
        settings = patch.dict('os.environ', POCKLO_CLIENT_ID='test-app', POCKLO_CLIENT_SECRET='test-secret',
                              POCKLO_STORAGE_KEY=Fernet.generate_key().decode(), POCKLO_DOWNLOAD_ORIGIN='https://storage.example')
        settings.start()
        self.addCleanup(settings.stop)
        self.first = self.account('first', '13812345678')
        self.second = self.account('second', '+8613812345678')
        self.subject = 'pocklo:6269bb3c-29ef-423a-8197-60bd89b1e217'

    @staticmethod
    def account(slug, phone):
        space = Space.objects.create(slug=slug, name=slug, email=f'{slug}@example.com')
        user = User.create(space, slug, verified=True)
        user.phone = phone
        user.phone_verified_at = timezone.now()
        user.save(update_fields=['phone', 'phone_verified_at'])
        return user

    def begin(self, user):
        url = start(user, 'https://frienden.jyonn.space', f'/{user.space.slug}/app/chats/123')
        return parse_qs(urlsplit(url).query)['state'][0]

    def provider(self, path, *, data):
        if path == 'token/':
            return {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 900, 'scope': 'files.read shares.create'}
        self.assertEqual(path, 'frienden-account/')
        self.assertEqual(data, {'token': 'access', 'phone': '+8613812345678'})
        return {'sub': self.subject}

    def test_shared_phone_reuses_credentials_but_each_space_connects_explicitly(self):
        with patch('User.pocklo.remote', side_effect=self.provider):
            finish(*consume(self.begin(self.first)), 'code')
            self.assertEqual(status(self.first), 'connected')
            self.assertEqual(status(self.second), 'connect_required')
            finish(*consume(self.begin(self.second)), 'code')
            self.assertEqual(PockloCredential.objects.count(), 1)
            self.assertEqual(PockloConnection.objects.filter(credential__isnull=False).count(), 2)
            self.assertEqual(access_for(self.second), 'access')

    def test_callback_is_single_use_and_a_new_connection_replaces_pending_operation(self):
        state = self.begin(self.first)
        operation = consume(state)
        with self.assertRaises(PockloError):
            consume(state)
        self.begin(self.first)
        with patch('User.pocklo.remote') as provider, self.assertRaises(PockloError):
            finish(*operation, 'code')
        provider.assert_not_called()

    def test_phone_change_between_start_and_callback_blocks_provider_exchange(self):
        operation = consume(self.begin(self.first))
        User.objects.filter(pk=self.first.pk).update(phone='13912345678')
        with patch('User.pocklo.remote') as provider, self.assertRaises(PockloError):
            finish(*operation, 'code')
        provider.assert_not_called()

    def test_rejects_untrusted_return_target_and_download(self):
        with self.assertRaises(PockloError):
            start(self.first, 'https://other.example', '/app/menu')
        with self.assertRaises(PockloError):
            start(self.first, 'https://frienden.jyonn.space', '//other.example/app/menu')
        token = '6269bb3c-29ef-423a-8197-60bd89b1e217'
        self.assertIsNone(share_token(f'https://pocklo.jyonn.space@other.example/share/{token}'))
        with patch('Message.providers.pocklo.public_body', side_effect=[
            {'resource': {'id': 'file', 'name': 'File', 'folder': False}},
            {'kind': 'download', 'url': 'https://res.6-79.cn@other.example/file'},
        ]), self.assertRaises(ValueError):
            download_target(token)

    def test_public_folder_preview_has_no_download(self):
        token = '6269bb3c-29ef-423a-8197-60bd89b1e217'
        with patch('Message.providers.pocklo.public_body', return_value={
            'resource': {'id': 'folder', 'name': 'Folder', 'folder': True},
        }) as fetch:
            self.assertEqual(preview(token)['provider_data']['kind'], 'folder')
            with self.assertRaises(ValueError):
                download_target(token)
            self.assertEqual(fetch.call_count, 2)

    def test_changed_pocklo_phone_invalidates_local_connection(self):
        with patch('User.pocklo.remote', side_effect=self.provider):
            finish(*consume(self.begin(self.first)), 'code')
        with patch('User.pocklo.remote', side_effect=PockloError('account_mismatch')), self.assertRaises(PockloError):
            access_for(self.first)
        self.assertEqual(status(self.first), 'connect_required')
