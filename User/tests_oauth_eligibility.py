import base64
import hashlib
import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from django.test import TestCase, RequestFactory
from django.utils import timezone

from Space.models import Space
from User.models import User, OAuthGrant, UserAvatarTypeChoice
from User.management.commands.seed_oauth_applications import seed
from User.oauth import authorize, exchange, userinfo, revoke, OAuthError
from User.oauth_eligibility import eligibility
from User.oauth_avatar import profile_avatar_url
from utils.auth import get_login_token


class OAuthEligibilityTests(TestCase):
    def setUp(self):
        seed('https://stillfolio.jyonn.space/auth/sermo/callback')
        space = Space.objects.create(name='Eligibility test', slug='eligibility', email='owner@example.test')
        self.user = User.objects.create(space=space, name='Synthetic', email='verified@example.test',
                                       phone='+8613800138000', email_verified_at=timezone.now(), phone_verified_at=timezone.now())
        self.verifier = 'v' * 43
        self.params = dict(client_id='chronelle', redirect_uri='https://chronelle.jyonn.space/api/auth/friendom/callback/',
                           scope='profile', state='s' * 32,
                           code_challenge=base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).rstrip(b'=').decode(),
                           code_challenge_method='S256', response_type='code')
        from User.oauth import registered_redirect_uri
        self.params['redirect_uri'] = registered_redirect_uri('chronelle')
        login = get_login_token(self.user)
        self.login = login['auth']
        self.refresh = login['refresh']

    def change(self, **fields):
        User.objects.filter(pk=self.user.pk).update(**fields)
        # Deliberately keep self.user stale to prove authorization rereads DB state.

    def code(self):
        return parse_qs(urlsplit(authorize(self.user, **self.params)).query)['code'][0]

    def exchange(self, code):
        return exchange(client_id=self.params['client_id'], redirect_uri=self.params['redirect_uri'], code=code, code_verifier=self.verifier)

    def assert_required(self, call):
        with self.assertRaises(OAuthError) as error:
            call()
        self.assertEqual(error.exception.code, 'verification_required')

    def test_each_verification_and_contact_value_is_required_at_authorization(self):
        original = {key: getattr(self.user, key) for key in ('email', 'phone', 'email_verified_at', 'phone_verified_at')}
        for fields in ({'email_verified_at': None}, {'phone_verified_at': None},
                       {'email_verified_at': None, 'phone_verified_at': None}, {'email': ''}, {'phone': '  '}):
            with self.subTest(fields=fields):
                self.change(**original)
                self.change(**fields)
                self.assert_required(self.code)
        self.assertEqual(OAuthGrant.objects.count(), 0)

    def test_exchange_rechecks_user_and_does_not_consume_ineligible_code(self):
        code = self.code()
        self.change(phone_verified_at=None)
        response = self.client.post('/users/oauth/token', json.dumps(dict(grant_type='authorization_code',
                                    client_id=self.params['client_id'], redirect_uri=self.params['redirect_uri'],
                                    code=code, code_verifier=self.verifier)), content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'verification_required')
        self.assert_required(lambda: self.exchange(code))
        grant = OAuthGrant.objects.get()
        self.assertIsNone(grant.code_used_at)
        self.assertIsNone(grant.access_token_hash)
        self.change(phone_verified_at=timezone.now())
        self.assertIn('access_token', self.exchange(code))

    def test_preexisting_token_is_blocked_without_mutating_grant_and_recovers_after_verification(self):
        token = self.exchange(self.code())['access_token']
        snapshot = list(OAuthGrant.objects.values())
        self.change(email_verified_at=None)
        self.assert_required(lambda: userinfo(token))
        response = self.client.get('/users/oauth/userinfo', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'verification_required')
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(list(OAuthGrant.objects.values()), snapshot)
        self.change(email_verified_at=timezone.now())
        self.assertIn('sub', userinfo(token))

    def test_legacy_raw_grant_cannot_bypass_and_revocation_remains_available(self):
        token = 'synthetic-legacy-token'
        OAuthGrant.objects.create(user=self.user, client_id='chronelle', redirect_uri=self.params['redirect_uri'],
                                  scopes=['profile'], code_challenge='v' * 43, code_hash=hashlib.sha256(b'old-code').hexdigest(),
                                  code_expires_at=timezone.now(), access_token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                  access_expires_at=timezone.now() + timedelta(hours=1))
        self.change(phone_verified_at=None)
        self.assert_required(lambda: userinfo(token))
        revoke(token)
        self.assertIsNotNone(OAuthGrant.objects.get().revoked_at)

    def test_http_reporting_enforcement_and_normal_login_are_separate(self):
        self.change(phone_verified_at=None)
        headers = {'HTTP_AUTHORIZATION': f'Bearer {self.login}'}
        status = self.client.get('/users/oauth/eligibility', **headers)
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()['missing_verifications'], ['phone'])
        self.assertFalse(status.json()['eligible'])
        response = self.client.post('/users/oauth/authorize', json.dumps(self.params), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 403)
        self.assertIn('account-security', response.json()['verification_path'])
        # An ordinary Frienden bearer still authenticates without dual verification.
        from utils.auth import require_user, refresh_login_token
        request = RequestFactory().get('/', **headers)
        self.assertEqual(require_user(lambda current: current.user.pk)(request), self.user.pk)
        # Registry management is a separate, dev-space-only capability.
        self.assertEqual(self.client.get('/users/oauth/applications', **headers).status_code, 403)
        self.assertIn('auth', refresh_login_token(self.refresh))

    def test_refresh_grant_type_remains_unsupported(self):
        response = self.client.post('/users/oauth/token', {'grant_type': 'refresh_token', 'refresh_token': 'synthetic'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'unsupported_grant_type')

    @patch('utils.qiniu._required_config', side_effect=lambda key: 'avatars.example.test')
    def test_old_signed_custom_avatar_cannot_bypass_current_eligibility(self, _config):
        self.user.avatar_type = UserAvatarTypeChoice.CUSTOM
        self.user.avatar_uri = 'https://avatars.example.test/sermo/avatar/synthetic.png'
        self.user.save(update_fields=['avatar_type', 'avatar_uri'])
        url = profile_avatar_url(self.user)
        self.assertIsNotNone(url)
        self.change(phone_verified_at=None)
        with patch('User.oauth_avatar.requests.get') as fetch:
            response = self.client.get(urlsplit(url).path)
        self.assertEqual(response.status_code, 404)
        fetch.assert_not_called()
