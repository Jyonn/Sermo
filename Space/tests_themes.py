import json

from django.test import TestCase, RequestFactory
from Config.models import Config, CI

from PlatformAdmin.models import PlatformAuditLog
from PlatformAdmin.views import SpaceFeatureGrantView
from Space.models import Space, SpaceFeatureGrant, SpaceFeatureKeyChoice
from User.models import User
from utils.auth import get_login_token, get_space_login_token, get_platform_admin_token


class SpaceThemeTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Theme', slug='theme', email='theme@example.test')
        self.other = Space.objects.create(name='Other', slug='other', email='other@example.test')
        self.user = User.create(self.space, 'Member')

    def settings(self, enabled=None, token=None):
        payload = dict(name=self.space.name, group_square_enabled=0, chat_enabled=1,
                       square_explore_enabled=0, member_limit=5, level_names=self.space.level_names)
        if enabled is not None:
            payload['qixi_theme_enabled'] = enabled
        return self.client.post('/spaces/admin/settings', json.dumps(payload), content_type='application/json',
                                HTTP_AUTHORIZATION='Bearer ' + (token or get_space_login_token(self.space)['auth']))

    def grant(self, granted=True):
        return SpaceFeatureGrant.set_granted(self.space, SpaceFeatureKeyChoice.QIXI_THEME, granted, 'admin@example.test')

    def test_default_and_unauthorized_enable(self):
        self.assertEqual(self.space.jsonl()['site_theme'], 'default')
        self.assertEqual(self.settings(1).status_code, 403)
        self.assertEqual(self.settings(0).status_code, 200)
        self.assertEqual(self.settings(2).status_code, 400)

    def test_grant_does_not_activate_then_admin_can_switch(self):
        self.grant()
        self.assertEqual(self.space.site_theme, 'default')
        response = self.settings(1)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['site_theme'], 'qixi-2026')
        response = self.client.get('/spaces/me', HTTP_AUTHORIZATION='Bearer ' + get_login_token(self.user)['auth'])
        self.assertEqual(response.json()['body']['site_theme'], 'qixi-2026')
        self.assertEqual(self.settings(0).json()['body']['site_theme'], 'default')

    def test_omitted_setting_is_preserved_and_other_spaces_remain_default(self):
        self.grant()
        self.settings(1)
        self.assertEqual(self.settings().json()['body']['site_theme'], 'qixi-2026')
        self.assertEqual(self.other.site_theme, 'default')

    def test_revocation_resets_and_regrant_cannot_restore_stale_theme(self):
        self.grant()
        self.settings(1)
        self.grant(False)
        self.space.refresh_from_db()
        self.assertFalse(self.space.qixi_theme_enabled)
        self.assertEqual(self.space.site_theme, 'default')
        self.grant()
        self.assertEqual(self.space.site_theme, 'default')
        self.settings(1)
        self.space.refresh_from_db()
        self.grant()
        self.assertEqual(self.space.site_theme, 'qixi-2026')

    def test_ungranted_stale_value_never_reported_to_members(self):
        Space.objects.filter(pk=self.space.pk).update(qixi_theme_enabled=True)
        self.space.refresh_from_db()
        self.assertEqual(self.space.site_theme, 'default')
        self.grant()
        self.assertFalse(self.space.qixi_theme_enabled)

    def test_member_cannot_switch_or_grant(self):
        self.grant()
        member_token = get_login_token(self.user)['auth']
        self.assertNotEqual(self.settings(1, member_token).status_code, 200)
        response = self.client.post(f'/platform-admin/spaces/{self.space.id}/features/qixi_theme',
                                    '{"enabled":true}', content_type='application/json',
                                    HTTP_AUTHORIZATION='Bearer ' + member_token)
        self.assertNotEqual(response.status_code, 200)

    def test_platform_grant_endpoint_is_audited(self):
        Config.objects.update_or_create(key=CI.ADMIN_EMAIL, defaults={'value': 'admin@example.test'})
        token = get_platform_admin_token('admin@example.test')['auth']
        request = RequestFactory().post('/platform-admin/spaces/1/features/qixi_theme',
                                        '{"enabled":true}', content_type='application/json',
                                        HTTP_AUTHORIZATION='Bearer ' + token)
        result = SpaceFeatureGrantView.as_view()(request, space_id=self.space.id, feature_key='qixi_theme')
        self.assertTrue(result['qixi_theme_granted'])
        self.assertFalse(result['qixi_theme_enabled'])
        self.assertTrue(PlatformAuditLog.objects.filter(action='space.feature_granted',
                                                       metadata__feature_key='qixi_theme').exists())
