from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from Activity.models import ActivityAwakening, ActivityCampaign, ActivityEvent, ActivityMilestone, ActivityService, SpaceActivityReward, UserActivityReward
from Space.models import Space
from User.models import User, UserResourceInventory


class ActivityServiceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Test', slug='activity-test', email='admin@example.com')
        self.user = User.create(self.space, 'Member', email='member@example.com', verified=True)
        self.pool = [
            {'reward_id': 'bubble.one', 'resource_key': 'baxian-lv'},
            {'reward_id': 'bubble.two', 'resource_key': 'baxian-he'},
        ]
        self.campaign = ActivityCampaign.objects.create(
            key='test-campaign', title='Test', starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1), event_key='square.statement.publish',
            config={
                'personal_event_target': 2,
                'personal_reward_pool': [dict(item, resource_type='bubble') for item in self.pool],
            },
        )
        ActivityMilestone.objects.create(
            campaign=self.campaign, threshold=1, resource_type='bubble', reward_pool=self.pool,
        )

    def test_daily_event_is_idempotent_and_contribution_unlocks_for_space(self):
        ActivityService.record_event(self.user, 'square.statement.publish', '1')
        ActivityService.record_event(self.user, 'square.statement.publish', '2')
        ActivityService.record_event(self.user, 'square.statement.publish', '2')
        ActivityService.record_event(self.user, 'square.statement.publish', '3')
        self.assertEqual(ActivityEvent.objects.filter(campaign=self.campaign, progress__user=self.user).count(), 3)
        self.assertEqual(ActivityEvent.objects.filter(campaign=self.campaign, progress__user=self.user, points=1).count(), 1)
        self.assertEqual(UserActivityReward.objects.filter(progress__user=self.user, progress__campaign=self.campaign).count(), 1)

        payload = ActivityService.payload(self.campaign, self.user)
        self.assertTrue(payload['personal_reward_claimable'])
        self.assertIsNone(payload['personal_reward'])
        reward = ActivityService.claim_personal_reward(self.campaign, self.user)
        self.assertIsNotNone(reward.claimed_at)
        payload = ActivityService.payload(self.campaign, self.user)
        self.assertFalse(payload['personal_reward_claimable'])
        self.assertEqual(payload['personal_reward']['reward_id'], reward.reward_id)
        self.assertEqual(payload['official_user']['user_id'], self.space.official_user_id)
        self.assertEqual(payload['claimable_points'], 1)
        self.assertEqual(payload['available_points'], 0)
        self.assertEqual(ActivityService.contribute(self.campaign, self.user), 0)
        self.assertEqual(ActivityService.claim(self.campaign, self.user), 1)
        self.assertEqual(ActivityService.claim(self.campaign, self.user), 0)

        amount = ActivityService.contribute(self.campaign, self.user)
        self.assertEqual(amount, 1)
        payload = ActivityService.payload(self.campaign, self.user)
        self.assertEqual(payload['space_reward_claimable']['threshold'], 1)
        self.assertFalse(SpaceActivityReward.objects.filter(space_activity__space=self.space).exists())
        reward = ActivityService.claim_space_reward(self.campaign, self.user)
        self.assertEqual(reward.milestone.threshold, 1)
        self.assertIsNone(ActivityService.payload(self.campaign, self.user)['space_reward_claimable'])
        self.assertTrue(UserResourceInventory.objects.filter(
            user=self.user, source='activity', resource_key=reward.resource_key,
        ).exists())
        self.assertTrue(ActivityAwakening.objects.filter(space_activity__space=self.space, user=self.user).exists())

    def test_payload_exposes_space_official_user(self):
        official = self.space.ensure_official_user()

        payload = ActivityService.payload(self.campaign, self.user)

        self.assertEqual(payload['official_user']['user_id'], official.id)
