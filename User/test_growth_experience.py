from django.test import TestCase, RequestFactory
from smartdjango import Error
from Space.models import Space
from User.models import User, UserResourceInventory
from User.growth import LEVEL_REWARDS
from User.growth_report import growth_report
from User.views import UserGrowthReportView
from utils import auth


class GrowthExperienceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Growth Journey', slug='journey', email='owner@example.com')
        self.user = User.objects.create(space=self.space, name='Member', lower_name='member')

    def test_catalog_exposes_completed_rewards_without_frame_collection(self):
        rewards = {reward['id']: reward for items in LEVEL_REWARDS.values() for reward in items}
        self.assertNotIn('frame.collection', rewards)
        for key in ['growth.report', 'profile.final_slot', 'identity.final_badge']:
            self.assertEqual(rewards[key]['implementation_status'], 'live')

    def test_report_is_locked_and_retains_earned_access(self):
        with self.assertRaises(Error):
            growth_report(self.user)
        UserResourceInventory.grant_growth_rewards(self.user, 16)
        self.user.award_growth('explore:install_webapp')
        other = User.objects.create(space=self.space, name='Other', lower_name='other')
        other.award_growth('explore:avatar')
        report = growth_report(self.user)
        self.assertTrue(self.user.calculate_growth()['report_available'])
        self.assertEqual(len(report['activity']), 30)
        self.assertEqual(report['record_count'], 1)
        self.assertEqual(report['categories'][0]['points'], 40)
        self.assertEqual(report['categories'][0]['category'], 'explore')
        self.assertEqual(sum(day['count'] for day in report['activity']), 1)
        self.assertNotIn('messages', report)

    def test_level_18_profile_honor_is_permanent_and_idempotent(self):
        self.assertIsNone(self.user.growth_honor())
        UserResourceInventory.grant_growth_rewards(self.user, 18)
        first = self.user.growth_honor()
        UserResourceInventory.grant_growth_rewards(self.user, 18)
        self.assertEqual(first, self.user.growth_honor())
        self.assertEqual(first['name'], self.space.level_names[17])
        self.assertEqual(self.user.jsonl()['growth_honor']['level'], 18)
        self.assertEqual(self.user.growth_level, 1)

    def test_report_endpoint_checks_the_current_user_not_a_query_user(self):
        request = RequestFactory().get('/users/me/growth-report?user_id=999', HTTP_AUTHORIZATION=f'Bearer {auth.get_login_token(self.user)["auth"]}')
        with self.assertRaises(Error):
            UserGrowthReportView.as_view()(request)
        UserResourceInventory.grant_growth_rewards(self.user, 16)
        request = RequestFactory().get('/users/me/growth-report?user_id=999', HTTP_AUTHORIZATION=f'Bearer {auth.get_login_token(self.user)["auth"]}')
        self.assertEqual(UserGrowthReportView.as_view()(request)['record_count'], 0)
