from django.test import RequestFactory, TestCase
from unittest.mock import patch, Mock

from Space.models import Space
from User.models import User
from User.chronelle_views import ChronelleBindingView
from utils import auth


class ChronelleBindingTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Calendar', slug='calendar-test', email='calendar@example.com')
        self.user = User.create(space=self.space, name='Calendar Owner', verified=True)
        self.other = User.create(space=self.space, name='Other Owner', verified=True)
        self.factory = RequestFactory()

    def test_binding_requires_authentication(self):
        with self.assertRaises(Exception):
            ChronelleBindingView().delete(self.factory.delete('/users/me/chronelle'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.chronelle_calendar_id, '')

    def test_unbind_only_changes_authenticated_user(self):
        calendar_id = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'
        User.objects.filter(pk__in=[self.user.pk, self.other.pk]).update(chronelle_calendar_id=calendar_id)
        response = self.client.delete('/users/me/chronelle', HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.user)['auth']}")
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.user.chronelle_calendar_id, '')
        self.assertEqual(self.other.chronelle_calendar_id, calendar_id)

    def test_pkce_authorization_binds_without_publishing_key(self):
        calendar_id = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'
        headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.user)['auth']}"}
        with patch('User.chronelle_views.requests.post', return_value=Mock(status_code=201, json=lambda: {'request_id': calendar_id})) as remote:
            result = self.client.post('/users/me/chronelle/authorize', **headers)
            self.assertIn('/#/authorize/', result.json()['body']['authorize_url'])
            self.assertEqual(remote.call_args.kwargs['json']['code_challenge_method'], 'S256')
        self.user.refresh_from_db()
        pending = self.user.chronelle_authorization
        self.assertNotIn('verifier', result.json()['body'])
        with patch('User.chronelle_views.requests.post', return_value=Mock(status_code=200, json=lambda: {'calendar': calendar_id, 'state': pending['state'], 'key': 'private-read-key'})):
            result = self.client.post('/users/me/chronelle/claim', **headers)
        self.assertEqual(result.json()['body']['status'], 'approved')
        self.assertNotIn('private-read-key', result.content.decode())
        self.user.refresh_from_db()
        self.assertEqual(self.user.chronelle_calendar_id, calendar_id)
        self.assertEqual(self.user.chronelle_authorization, {})

    def test_claim_does_not_use_another_accounts_authorization(self):
        self.other.chronelle_authorization = {'request_id': 'other', 'expires_at': 9999999999}
        self.other.save(update_fields=['chronelle_authorization'])
        with patch('User.chronelle_views.requests.post') as remote:
            result = self.client.post('/users/me/chronelle/claim', HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.user)['auth']}")
        self.assertEqual(result.json()['body']['status'], 'expired')
        remote.assert_not_called()

    def test_claim_rejects_mismatched_state(self):
        self.user.chronelle_authorization = {'request_id': '35c6deab-103d-4f60-9cf9-9f42c812cc5a', 'verifier': 'secret', 'state': 'expected', 'expires_at': 9999999999}
        self.user.save(update_fields=['chronelle_authorization'])
        with patch('User.chronelle_views.requests.post', return_value=Mock(status_code=200, json=lambda: {'state': 'wrong', 'calendar': '35c6deab-103d-4f60-9cf9-9f42c812cc5a'})):
            self.client.post('/users/me/chronelle/claim', HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.user)['auth']}")
        self.user.refresh_from_db()
        self.assertEqual(self.user.chronelle_calendar_id, '')

    def test_public_profile_serialization_contains_only_calendar_id(self):
        self.user.chronelle_calendar_id = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'
        self.assertEqual(self.user.jsonl()['chronelle_calendar_id'], self.user.chronelle_calendar_id)
        self.assertEqual(self.user.json_friend()['chronelle_calendar_id'], self.user.chronelle_calendar_id)
