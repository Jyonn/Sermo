from django.test import RequestFactory, TestCase

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

    def test_bind_persists_and_rejects_private_keys(self):
        calendar_id = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'
        headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.user)['auth']}"}
        response = self.client.post('/users/me/chronelle', {'url': f'https://chronelle.jyonn.space/#/share/{calendar_id}'}, content_type='application/json', **headers)
        self.assertEqual(response.json()['body']['chronelle_calendar_id'], calendar_id)
        self.user.refresh_from_db()
        self.assertEqual(self.user.chronelle_calendar_id, calendar_id)
        self.client.post('/users/me/chronelle', {'url': f'https://chronelle.jyonn.space/#/share/{calendar_id}?key=private'}, content_type='application/json', **headers)
        self.user.refresh_from_db()
        self.assertEqual(self.user.chronelle_calendar_id, calendar_id)

    def test_public_profile_serialization_contains_only_calendar_id(self):
        self.user.chronelle_calendar_id = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'
        self.assertEqual(self.user.jsonl()['chronelle_calendar_id'], self.user.chronelle_calendar_id)
        self.assertEqual(self.user.json_friend()['chronelle_calendar_id'], self.user.chronelle_calendar_id)
