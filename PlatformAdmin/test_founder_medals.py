import json
from django.test import TestCase, RequestFactory
from django.utils import timezone
from Config.models import CI, Config
from PlatformAdmin.views import FounderMedalView
from PlatformAdmin.models import PlatformAuditLog
from Space.models import Space
from User.models import User, UserResourceInventory
from utils import auth
from smartdjango import Error


class FounderMedalTests(TestCase):
    def setUp(self):
        Config.objects.update_or_create(key=CI.ADMIN_EMAIL, defaults={'value': 'admin@example.com'})
        self.space = Space.objects.create(name='Medal Space', slug='medals', email='owner@example.com')
        self.user = User.objects.create(space=self.space, name='Founder', lower_name='founder')
        self.token = auth.get_platform_admin_token('admin@example.com')['auth']

    def update(self, year=2026, enabled=True, token=None):
        request = RequestFactory().post('/platform-admin/members/1/founder-medal',
            data=json.dumps({'year': year, 'enabled': enabled}), content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token or self.token}')
        return FounderMedalView.as_view()(request, user_id=self.user.id)

    def test_grant_is_idempotent_public_and_audited(self):
        for _ in range(2):
            self.assertEqual(self.update()['founder_medal_years'], [2026])
        self.assertEqual(UserResourceInventory.objects.filter(user=self.user, reward_id='identity.founder.2026').count(), 1)
        self.assertEqual(self.user.jsonl()['founder_medal_years'], [2026])
        self.assertTrue(PlatformAuditLog.objects.filter(action='member.founder_medal_granted').exists())
        self.assertEqual(self.user.tiny_json()['growth_level_name'], self.space.level_names[0])
        other = User.objects.create(space=self.space, name='Other', lower_name='other')
        self.assertEqual(other.founder_medal_years(), [])
        cached = User.objects.prefetch_related('resource_inventory').get(id=self.user.id)
        self.assertEqual(cached.founder_medal_years(), [2026])

    def test_revocation_retains_history_and_regrant_restores(self):
        self.update()
        self.assertEqual(self.update(enabled=False)['founder_medal_years'], [])
        self.assertTrue(UserResourceInventory.objects.filter(user=self.user, reward_id='identity.founder.2026').exists())
        self.assertEqual(self.update()['founder_medal_years'], [2026])

    def test_ceremony_acknowledgement_is_persistent_and_idempotent(self):
        self.update()
        self.assertEqual(self.user.calculate_growth()['pending_founder_medal_years'], [2026])
        self.assertEqual(self.user.acknowledge_founder_medal(2026)['pending_founder_medal_years'], [])
        self.assertEqual(self.user.acknowledge_founder_medal(2026)['pending_founder_medal_years'], [])
        self.update()
        self.assertEqual(self.user.founder_medal_years(pending_only=True), [])
        self.update(enabled=False)
        with self.assertRaises(Error):
            self.user.acknowledge_founder_medal(2026)
        self.update()
        self.assertEqual(self.user.founder_medal_years(pending_only=True), [2026])

    def test_cannot_acknowledge_unowned_or_malformed_awards(self):
        for year in [2026, True, '2026', None]:
            with self.assertRaises(Error):
                self.user.acknowledge_founder_medal(year)

    def test_invalid_input_and_member_token_are_rejected(self):
        for year in [True, '2026', 2025, timezone.localdate().year + 1]:
            with self.assertRaises(Error):
                self.update(year=year)
        with self.assertRaises(Error):
            self.update(enabled='true')
        with self.assertRaises(Error):
            self.update(token=auth.get_login_token(self.user)['auth'])
        self.assertEqual(UserResourceInventory.objects.filter(user=self.user).count(), 0)
