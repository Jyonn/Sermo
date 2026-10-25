import json

from django.test import TestCase

from Space.models import Space, SpaceFeatureGrant, SpaceFeatureKeyChoice
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

    def grant(self, space, granted=True):
        SpaceFeatureGrant.set_granted(space, SpaceFeatureKeyChoice.OAUTH_DEVELOPER, granted, 'admin@example.test')

    def test_default_disabled_regardless_of_slug(self):
        self.assertFalse(self.dev.oauth_developer_available)
        self.assertFalse(self.dev.json_private()['oauth_developer_eligible'])
        self.assertFalse(self.other.json_private()['oauth_developer_eligible'])
        self.assertEqual(self.client.get('/users/oauth/applications', **self.headers(self.user)).status_code, 403)

    def test_admin_enable_and_disable_enforce_both_reads_and_writes(self):
        self.grant(self.dev)
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

    def test_granted_non_dev_space_can_enable_and_revoke_blocks_access(self):
        self.grant(self.other)
        self.assertTrue(self.other.oauth_developer_eligible)
        self.assertFalse(self.other.oauth_developer_available)
        self.assertEqual(self.settings(self.other, 1).status_code, 200)
        self.other.refresh_from_db()
        self.assertTrue(self.other.oauth_developer_available)
        self.assertEqual(self.client.get('/users/oauth/applications', **self.headers(self.other_user)).status_code, 200)
        self.grant(self.other, False)
        self.assertFalse(self.other.oauth_developer_enabled)
        for method in ('get', 'post'):
            self.assertEqual(getattr(self.client, method)('/users/oauth/applications', **self.headers(self.other_user)).status_code, 403)
        self.grant(self.other)
        self.assertFalse(self.other.oauth_developer_available)

    def test_new_grant_clears_stale_setting_but_repeat_grant_preserves_enable(self):
        self.other.oauth_developer_enabled = True
        self.other.save(update_fields=['oauth_developer_enabled'])
        self.grant(self.other)
        self.assertFalse(self.other.oauth_developer_enabled)
        self.assertEqual(self.settings(self.other, 1).status_code, 200)
        self.other.refresh_from_db()
        self.grant(self.other)
        self.assertTrue(self.other.oauth_developer_available)
        self.assertFalse(self.other.qq_binding_granted)
