import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from User.models import OAuthGrant, User
from User.oauth import OAuthError, authorize, exchange, registered_redirect_uri, revoke, userinfo
from utils.auth import get_login_token


class OAuthTests(TestCase):
    def setUp(self):
        from User.management.commands.seed_oauth_applications import seed
        seed('https://stillfolio.jyonn.space/auth/sermo/callback')
        self.space = Space.objects.create(name='OAuth test', slug='oauth-test', email='oauth@example.com')
        self.user = User.create(self.space, 'Photographer', verified=True)
        User.objects.filter(pk=self.user.pk).update(email='verified@example.test', email_verified_at=timezone.now(), phone='+8613800138000', phone_verified_at=timezone.now())
        self.user.refresh_from_db()
        self.verifier = 'v' * 43
        self.challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode()

    def authorize(self, **overrides):
        params = dict(client_id='stillfolio', redirect_uri=registered_redirect_uri(), scope='profile phone vip',
                      state='a' * 32, code_challenge=self.challenge, code_challenge_method='S256', response_type='code')
        params.update(overrides)
        return authorize(self.user, **params)

    def test_only_registered_redirect_and_pkce_are_allowed(self):
        with self.assertRaises(OAuthError):
            self.authorize(redirect_uri='https://evil.example/callback')
        with self.assertRaises(OAuthError):
            self.authorize(code_challenge_method='plain')
        with self.assertRaises(OAuthError):
            self.authorize(scope='profile email')
        self.assertEqual(OAuthGrant.objects.count(), 0)

    def test_code_is_single_use_and_userinfo_is_scoped(self):
        code = parse_qs(urlparse(self.authorize()).query)['code'][0]
        self.assertNotEqual(OAuthGrant.objects.get().code_hash, code)
        with self.assertRaises(OAuthError):
            exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier='x' * 43)
        result = exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier=self.verifier)
        with self.assertRaises(OAuthError):
            exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier=self.verifier)
        profile = userinfo(result['access_token'])
        self.assertEqual(profile['nickname'], 'Photographer')
        self.assertEqual(profile['phone'], '+8613800138000')
        self.assertFalse(profile['is_vip'])
        revoke(result['access_token'])
        with self.assertRaises(OAuthError):
            userinfo(result['access_token'])

    def test_verified_mainland_phone_only_and_scope_isolation(self):
        self.user.phone = '+8613800138000'
        self.user.is_permanent_vip = True
        self.user.save(update_fields=['phone', 'is_permanent_vip'])
        code = parse_qs(urlparse(self.authorize(scope='profile')).query)['code'][0]
        token = exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier=self.verifier)['access_token']
        self.assertNotIn('phone', userinfo(token))
        self.assertNotIn('is_vip', userinfo(token))
        self.user.phone_verified_at = timezone.now()
        self.user.save(update_fields=['phone_verified_at'])
        code = parse_qs(urlparse(self.authorize(scope='phone vip')).query)['code'][0]
        token = exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier=self.verifier)['access_token']
        self.assertEqual(userinfo(token)['phone'], '+8613800138000')
        self.assertTrue(userinfo(token)['is_vip'])

    def test_http_authorize_token_and_userinfo(self):
        request = dict(client_id='stillfolio', redirect_uri=registered_redirect_uri(), scope='profile',
                       state='a' * 32, code_challenge=self.challenge, code_challenge_method='S256', response_type='code')
        login_token = get_login_token(self.user)['auth']
        authorized = self.client.post('/users/oauth/authorize', json.dumps(request), content_type='application/json',
                                      HTTP_AUTHORIZATION=f'Bearer {login_token}')
        self.assertEqual(authorized.status_code, 200)
        code = parse_qs(urlparse(authorized.json()['redirect_uri']).query)['code'][0]
        token_request = dict(grant_type='authorization_code', client_id='stillfolio',
                             redirect_uri=registered_redirect_uri(), code=code, code_verifier=self.verifier)
        token_response = self.client.post('/users/oauth/token', json.dumps(token_request), content_type='application/json')
        self.assertEqual(token_response.status_code, 200)
        access_token = token_response.json()['access_token']
        self.assertEqual(self.client.post('/users/oauth/token', json.dumps(token_request), content_type='application/json').status_code, 400)
        profile = self.client.get('/users/oauth/userinfo', HTTP_AUTHORIZATION=f'Bearer {access_token}')
        self.assertEqual(profile.json()['nickname'], 'Photographer')

    def test_chronelle_profile_exchange_and_client_isolation(self):
        callback = registered_redirect_uri('chronelle')
        redirect = self.authorize(client_id='chronelle', redirect_uri=callback, scope='profile')
        code = parse_qs(urlparse(redirect).query)['code'][0]
        with self.assertRaises(OAuthError):
            exchange(client_id='stillfolio', redirect_uri=registered_redirect_uri(), code=code, code_verifier=self.verifier)
        result = exchange(client_id='chronelle', redirect_uri=callback, code=code, code_verifier=self.verifier)
        self.assertEqual(result['scope'], 'profile')
        profile = userinfo(result['access_token'])
        self.assertIn('avatar_url', profile)
        self.assertNotIn('phone', profile)
        self.assertNotIn('is_vip', profile)
        with self.assertRaises(OAuthError):
            exchange(client_id='chronelle', redirect_uri=callback, code=code, code_verifier=self.verifier)

    def test_chronelle_requires_exact_callback_profile_only_and_s256(self):
        callback = registered_redirect_uri('chronelle')
        for overrides in ({'scope': 'phone'}, {'scope': 'profile vip'}, {'redirect_uri': callback.rstrip('/')},
                          {'redirect_uri': callback + '?extra=1'}, {'redirect_uri': registered_redirect_uri()},
                          {'code_challenge_method': 'plain'}):
            values = {'client_id': 'chronelle', 'redirect_uri': callback, 'scope': 'profile', **overrides}
            with self.subTest(overrides=overrides), self.assertRaises(OAuthError):
                self.authorize(**values)
        self.assertEqual(OAuthGrant.objects.count(), 0)

    def test_all_legacy_clients_http_roundtrip_and_cross_client_code_isolation(self):
        login_token = get_login_token(self.user)['auth']
        for client_id, scope in [('stillfolio', 'profile phone vip'), ('chronelle', 'profile'), ('supervision-local', 'profile')]:
            with self.subTest(client_id=client_id):
                callback = registered_redirect_uri(client_id)
                params = dict(client_id=client_id, redirect_uri=callback, scope=scope, state='s' * 32,
                              code_challenge=self.challenge, code_challenge_method='S256', response_type='code')
                metadata = self.client.get('/users/oauth/metadata', params)
                self.assertEqual(metadata.status_code, 200)
                self.assertEqual(metadata.json()['callback'], callback)
                response = self.client.post('/users/oauth/authorize', json.dumps(params), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {login_token}')
                self.assertEqual(response.status_code, 200)
                destination = urlparse(response.json()['redirect_uri'])
                self.assertEqual(parse_qs(destination.query)['state'], ['s' * 32])
                code = parse_qs(destination.query)['code'][0]
                for other in {'stillfolio', 'chronelle', 'supervision-local'} - {client_id}:
                    with self.assertRaises(OAuthError):
                        exchange(client_id=other, redirect_uri=registered_redirect_uri(other), code=code, code_verifier=self.verifier)
                result = exchange(client_id=client_id, redirect_uri=callback, code=code, code_verifier=self.verifier)
                self.assertEqual(set(result['scope'].split()), set(scope.split()))
                profile = userinfo(result['access_token'])
                self.assertEqual(profile['sub'], f'sermo:{self.space.id}:{self.user.id}')
                self.assertNotIn('email', profile)
                self.assertNotIn('email_verified', profile)
                if client_id != 'stillfolio':
                    self.assertNotIn('phone', profile)
                    self.assertNotIn('is_vip', profile)
                with self.assertRaises(OAuthError):
                    exchange(client_id=client_id, redirect_uri=callback, code=code, code_verifier=self.verifier)
                revoke(result['access_token'])
                with self.assertRaises(OAuthError):
                    userinfo(result['access_token'])

    def test_all_legacy_clients_reject_email_scope_without_registry_revision(self):
        for client_id in ('stillfolio', 'chronelle', 'supervision-local'):
            with self.subTest(client_id=client_id), self.assertRaises(OAuthError):
                self.authorize(client_id=client_id, redirect_uri=registered_redirect_uri(client_id), scope='profile email')
