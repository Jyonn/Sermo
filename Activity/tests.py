from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from Activity.models import ActivityAwakening, ActivityCampaign, ActivityEvent, ActivityMilestone, ActivityService, UserActivityReward
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
        self.assertEqual(UserActivityReward.objects.filter(progress__user=self.user).count(), 1)

        amount = ActivityService.contribute(self.campaign, self.user)
        self.assertEqual(amount, 1)
        self.assertTrue(UserResourceInventory.objects.filter(user=self.user, source='activity').exists())
        self.assertTrue(ActivityAwakening.objects.filter(space_activity__space=self.space, user=self.user).exists())
