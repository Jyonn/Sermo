import json
import time
from unittest.mock import patch
from urllib.parse import urlsplit, parse_qs
from cryptography.fernet import Fernet
from django.test import TestCase
from Space.models import Space
from User.models import User, DropConnection
from User.drop import binding, share_url
from User.external_account_binding import ExternalBindingError
from utils import auth


class DropPickerTests(TestCase):
    entry = 'e08e9580-3df8-4ae6-8d7b-9d1a7c25ad9a'
    url = 'https://drop.jyonn.space/?share=' + 'a' * 43

    def setUp(self):
        config = patch.object(binding, 'config', return_value=('fixture-client', 'fixture-secret', Fernet(Fernet.generate_key())))
        config.start(); self.addCleanup(config.stop)
        self.user = self.account('drop-first'); self.other = self.account('drop-other')

    def account(self, slug):
        space = Space.objects.create(slug=slug, name=slug, email=slug+'@example.test')
        return User.create(space, slug, verified=True)

    def headers(self, user=None):
        return {'HTTP_AUTHORIZATION': 'Bearer '+auth.get_login_token(user or self.user)['auth']}

    def post(self, path, data, user=None):
        return self.client.post(path, json.dumps(data), content_type='application/json', **self.headers(user))

    def tokens(self, access='access', refresh='refresh'):
        return dict(access_token=access, refresh_token=refresh, expires_in=900, scope='diary.picker shares.create')

    def connected(self, expires=None):
        value = binding.credential(self.tokens())
        if expires is not None: value['expires'] = expires
        DropConnection.objects.update_or_create(user=self.user, defaults={'sealed': binding.seal(value)})

    def test_pkce_callback_scope_origin_and_replay_without_phone_proof(self):
        start = self.post('/users/me/drop', dict(action='connect', origin='https://frienden.jyonn.space', path='/drop-first/app/chats/42'))
        self.assertEqual(start.status_code, 200)
        query = parse_qs(urlsplit(start.json()['authorize_url']).query)
        self.assertEqual(query['scope'], ['diary.picker shares.create'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(len(query['code_challenge'][0]), 43)
        with patch.object(binding, 'remote', return_value=self.tokens()) as remote:
            response = self.client.get('/users/drop/callback', {'state': query['state'][0], 'code': 'fixture'})
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response['Location'], 'https://frienden.jyonn.space/drop-first/app/chats/42?drop=connected')
            self.assertEqual(remote.call_count, 1)
            self.assertEqual(remote.call_args.args, ('oauth/token/',))
            self.assertIn('code_verifier', remote.call_args.kwargs['data'])
            self.assertEqual(self.client.get('/users/drop/callback', {'state': query['state'][0], 'code': 'fixture'}).status_code, 409)
        self.assertEqual(binding.status(self.user), 'connected')
        self.assertEqual(binding.status(self.other), 'connect_required')
        self.assertEqual(self.post('/users/me/drop', dict(action='connect', origin='https://evil.test', path='/app/menu')).status_code, 400)
        self.assertEqual(self.client.get('/users/drop/callback?state=x&state=y').status_code, 400)

    def test_deny_consumes_state_and_wrong_state_does_not_link(self):
        url = binding.start(self.user, 'https://frienden.6-79.cn', '/app/menu')
        state = parse_qs(urlsplit(url).query)['state'][0]
        self.assertEqual(self.client.get('/users/drop/callback', {'state': state+'bad', 'code': 'code'}).status_code, 409)
        denied = self.client.get('/users/drop/callback', {'state': state, 'error': 'access_denied'})
        self.assertIn('drop=denied', denied['Location'])
        self.assertEqual(binding.status(self.user), 'connect_required')
        self.assertEqual(self.client.get('/users/drop/callback', {'state': state, 'code': 'code'}).status_code, 409)

    def test_refresh_once_and_uncertain_rotation_is_not_replayed(self):
        self.connected(time.time()-1)
        with patch.object(binding, 'remote', return_value=self.tokens('next', 'rotated')) as remote:
            self.assertEqual(binding.access_for(self.user), 'next')
            self.assertEqual(binding.access_for(self.user), 'next')
            self.assertEqual(remote.call_count, 1)
            self.assertEqual(binding.open_sealed(DropConnection.objects.get(user=self.user).sealed)['refresh'], 'rotated')
        self.connected(time.time()-1)
        with patch.object(binding, 'remote', side_effect=ExternalBindingError('unavailable', 503)) as remote:
            with self.assertRaises(ExternalBindingError): binding.access_for(self.user)
            with self.assertRaises(ExternalBindingError): binding.access_for(self.user)
            self.assertEqual(remote.call_count, 1)
        self.assertEqual(DropConnection.objects.get(user=self.user).sealed, '')

    def test_entries_limits_account_isolation_and_no_secret_response(self):
        self.connected()
        data = dict(entries=[dict(id=self.entry, text='合成日记', occurred_at='2026-11-15T12:00:00+08:00', has_audio=False, has_location=False)], allowed_fields=['text', 'occurred_at'], has_more=False, next_offset=None)
        with patch.object(binding, 'remote', return_value=data) as remote:
            response = self.client.get('/users/me/drop/entries', {'q': '合成', 'offset': '0'}, **self.headers())
            self.assertEqual(response.json(), data)
            self.assertEqual(response['Cache-Control'], 'no-store')
            self.assertEqual(remote.call_args.kwargs['params'], {'q': '合成', 'offset': 0})
            self.assertEqual(self.client.get('/users/me/drop/entries', **self.headers(self.other)).status_code, 409)
            self.assertEqual(remote.call_count, 1)
            for params in ({'q':'x'*201},{'offset':'100001'},{'offset':'-1'}):
                self.assertEqual(self.client.get('/users/me/drop/entries', params, **self.headers()).status_code, 400)

    def test_share_fields_limits_idempotency_and_upstream_errors(self):
        self.connected()
        data = dict(entry_ids=[self.entry], fields=['text', 'occurred_at'], expires_in=86400, operation_id='operation-1')
        share = dict(id='share-id', url=self.url, fields=data['fields'], expires_at='2026-11-16T12:00:00+08:00')
        with patch.object(binding, 'remote', return_value=share) as remote:
            self.assertEqual(self.post('/users/me/drop/shares', data).json(), share)
            key = remote.call_args.kwargs['idempotency_key']
            self.assertEqual(len(key), 64)
            self.post('/users/me/drop/shares', data)
            self.assertEqual(remote.call_args.kwargs['idempotency_key'], key)
            self.assertEqual(remote.call_args.kwargs['data'], {k:data[k] for k in ('entry_ids', 'fields', 'expires_in')})
            for changes in ({'fields':['text']},{'fields':['text','occurred_at','event']},{'expires_in':True},{'entry_ids':[self.entry,self.entry]},{'operation_id':'bad/key'}):
                self.assertEqual(self.post('/users/me/drop/shares', data|changes).status_code, 400)
        with patch.object(binding, 'remote', side_effect=ExternalBindingError('insufficient_fields', 403)):
            self.assertEqual(self.post('/users/me/drop/shares', data|{'fields':['text','occurred_at','audio']}).status_code, 403)
        with patch.object(binding, 'remote', side_effect=ExternalBindingError('share_gone', 410)):
            self.assertEqual(self.post('/users/me/drop/shares', data).status_code, 410)

    def test_disconnect_revokes_only_own_grant_and_does_not_revoke_public_shares(self):
        self.connected()
        with patch.object(binding, 'remote', return_value={}) as remote:
            self.assertEqual(self.post('/users/me/drop', {'action':'disconnect'}).status_code, 200)
            remote.assert_called_once_with('oauth/revoke/', data={'token':'refresh'})
        self.assertEqual(binding.status(self.user), 'connect_required')

    def test_strict_share_url_and_late_error_cannot_erase_reconnected_grant(self):
        self.assertEqual(share_url(self.url), self.url)
        for url in [self.url+'&x=1',self.url+'#hash',self.url.replace('drop.jyonn.space','drop.jyonn.space.evil.test'),self.url+'&share='+'b'*43]:
            self.assertIsNone(share_url(url))
        self.connected()
        binding.invalidate(self.user, 'old-access')
        self.assertEqual(binding.status(self.user), 'connected')
