import json

from django.test import TestCase

from Space.models import Space
from User.models import User
from utils.auth import get_login_token, get_space_login_token


class OAuthDeveloperCapabilityTests(TestCase):
    def setUp(self):
        self.dev = Space.objects.create(name='Developer', slug='dev', email='dev@example.test')
        self.other = Space.objects.create(name='Other', slug='other', email='other@example.test')
        self.user = User.create(self.dev, 'Developer')
        self.other_user = User.create(self.other, 'Other')

    def headers(self, user):
        return dict(HTTP_AUTHORIZATION='Bearer ' + get_login_token(user)['auth'])

    def settings(self, space, enabled, headers=None):
        return self.client.post('/spaces/admin/settings', json.dumps(dict(
            name=space.name, group_square_enabled=0, chat_enabled=1,
            square_explore_enabled=0, unverified_group_policy=2, member_limit=5,
            level_names=space.level_names, oauth_developer_enabled=enabled,
        )), content_type='application/json', **(headers or dict(HTTP_AUTHORIZATION='Bearer ' + get_space_login_token(space)['auth'])))

    def test_default_disabled_and_only_dev_eligible(self):
        self.assertFalse(self.dev.oauth_developer_available)
        self.assertTrue(self.dev.json_private()['oauth_developer_eligible'])
        self.assertFalse(self.other.json_private()['oauth_developer_eligible'])
        self.assertEqual(self.client.get('/users/oauth/applications', **self.headers(self.user)).status_code, 403)

    def test_admin_enable_and_disable_enforce_both_reads_and_writes(self):
        enabled = self.settings(self.dev, 1)
        self.assertEqual(enabled.status_code, 200, enabled.content)
        self.dev.refresh_from_db()
        self.assertTrue(self.dev.jsonl()['oauth_developer_available'])
        self.assertEqual(self.client.get('/users/oauth/applications', **self.headers(self.user)).status_code, 200)
        self.assertEqual(self.settings(self.dev, 0).status_code, 200)
        for path in ('/users/oauth/applications', '/users/oauth/applications/123'):
            denied = self.client.post(path, '{}', content_type='application/json', **self.headers(self.user))
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.client.get('/users/oauth/applications', **self.headers(self.user)).status_code, 403)

    def test_other_space_cannot_enable_even_with_admin_or_forged_flag(self):
        self.assertEqual(self.settings(self.other, 1).status_code, 403)
        Space.objects.filter(pk=self.other.pk).update(oauth_developer_enabled=True)
        self.assertEqual(self.client.get('/users/oauth/applications', **self.headers(self.other_user)).status_code, 403)
        self.assertEqual(self.client.post('/users/oauth/applications', '{}', content_type='application/json', **self.headers(self.other_user)).status_code, 403)

    def test_member_cannot_enable_capability(self):
        self.assertNotEqual(self.settings(self.dev, 1, self.headers(self.user)).status_code, 200)
        self.dev.refresh_from_db()
        self.assertFalse(self.dev.oauth_developer_enabled)
