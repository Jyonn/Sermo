from django.test import TestCase
from django.utils import timezone

from Activity.eligibility import missing_activity_requirements
from Activity.models import ActivityCampaign, ActivityService, SpaceActivity
from Space.models import Space
from utils import auth


class ActivityEligibilityTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Eligibility', slug='eligibility', email='eligibility@example.test', group_square_enabled=True, admin_phone_verified_at=timezone.now())
        self.campaign = ActivityCampaign.objects.create(key='eligible-posting', title='Posting', event_key='square.statement.publish')
        self.headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_space_login_token(self.space)['auth']}"}

    def status(self):
        return next(row for row in ActivityService.admin_payloads(self.space) if row['key'] == self.campaign.key)

    def test_disabled_square_and_free_post_are_reported_and_enforced(self):
        for field in ('group_square_enabled', 'square_free_post_enabled'):
            setattr(self.space, field, False)
            self.space.save(update_fields=[field])
            status = self.status()
            self.assertFalse(status['eligible'])
            self.assertEqual(status['missing_requirements'], missing_activity_requirements(self.campaign, self.space))
            response = self.client.post(f'/activities/admin/{self.campaign.key}/claim', **self.headers)
            self.assertEqual(response.status_code, 403, response.content)
            self.assertFalse(SpaceActivity.objects.filter(campaign=self.campaign, space=self.space).exists())
            setattr(self.space, field, True)
            self.space.save(update_fields=[field])
        self.assertTrue(self.status()['eligible'])
        self.assertEqual(self.client.post(f'/activities/admin/{self.campaign.key}/claim', **self.headers).status_code, 200)

    def test_email_tier_cannot_bypass_square_policy(self):
        self.space.admin_phone_verified_at = None
        self.assertIn('square', missing_activity_requirements(self.campaign, self.space))

    def test_chat_comment_and_submission_alternatives_use_actual_features(self):
        self.space.square_free_post_enabled = False
        self.campaign.event_key = 'square.statement.comment'
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), [])
        self.campaign.config = {'mode': 'annual_explorer'}
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), ['publish'])
        self.space.submission_enabled = True
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), [])
        self.space.group_square_enabled = False
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), ['publish'])
        self.campaign.config = {'mode': 'starry_night_streak'}
        self.campaign.event_key = 'chat.message.send'
        self.space.chat_enabled = False
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), ['chat'])

    def test_automatic_assignment_waits_until_eligible_and_keeps_existing_progress(self):
        self.campaign.assignment_mode = 'automatic'
        self.campaign.save(update_fields=['assignment_mode'])
        self.space.square_free_post_enabled = False
        ActivityService.ensure_automatic_for_space(self.space)
        self.assertFalse(SpaceActivity.objects.filter(campaign=self.campaign, space=self.space).exists())
        self.space.square_free_post_enabled = True
        ActivityService.ensure_automatic_for_space(self.space)
        run = SpaceActivity.objects.get(campaign=self.campaign, space=self.space)
        self.space.square_free_post_enabled = False
        ActivityService.ensure_automatic_for_space(self.space)
        self.assertEqual(ActivityService.claim_for_space(self.campaign, self.space).pk, run.pk)

    def test_explicit_requirements_and_game_without_posting_are_supported(self):
        self.campaign.config = {'requirements': ['chat', 'submission']}
        self.space.chat_enabled = False
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), ['chat', 'submission'])
        self.campaign.config = {'mode': 'number_duel'}
        self.space.group_square_enabled = False
        self.assertEqual(missing_activity_requirements(self.campaign, self.space), [])

    def test_claim_requires_space_admin_authentication(self):
        response = self.client.post(f'/activities/admin/{self.campaign.key}/claim')
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(SpaceActivity.objects.filter(campaign=self.campaign, space=self.space).exists())
