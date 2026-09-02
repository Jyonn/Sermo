from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Activity.models import ActivityAwakening, ActivityCampaign, ActivityEvent, ActivityMilestone, ActivityService, SpaceActivity, SpaceActivityReward, UserActivityReward
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
            key='test-campaign', title='Test', duration_seconds=2 * 24 * 60 * 60,
            event_key='square.statement.publish',
            config={
                'personal_event_target': 2,
                'personal_reward_pool': [dict(item, resource_type='bubble') for item in self.pool],
            },
        )
        ActivityMilestone.objects.create(
            campaign=self.campaign, threshold=1, resource_type='bubble', reward_pool=self.pool,
        )
        self.space_activity = ActivityService.claim_for_space(self.campaign, self.space)

    def authorization(self):
        from utils import auth

        return dict(HTTP_AUTHORIZATION=f'Bearer {auth.get_login_token(self.user)["auth"]}')

    def admin_authorization(self):
        from utils import auth

        return dict(HTTP_AUTHORIZATION=f'Bearer {auth.get_space_login_token(self.space)["auth"]}')

    def test_manual_campaign_only_appears_after_space_claim(self):
        campaign = ActivityCampaign.objects.create(
            key='manual-campaign',
            title='Manual',
            assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
            duration_seconds=3600,
            event_key='manual.event',
        )

        before = self.client.get('/activities/active', **self.authorization())
        self.assertNotIn(campaign.key, [item['key'] for item in before.json()['body']])

        claimed = self.client.post(f'/activities/admin/{campaign.key}/claim', **self.admin_authorization())
        self.assertEqual(claimed.status_code, 200, claimed.content)
        run = SpaceActivity.objects.get(campaign=campaign, space=self.space)
        self.assertEqual(int((run.ends_at - run.claimed_at).total_seconds()), 3600)

        after = self.client.get('/activities/active', **self.authorization())
        self.assertIn(campaign.key, [item['key'] for item in after.json()['body']])

    def test_unclaimed_manual_campaign_does_not_record_events(self):
        campaign = ActivityCampaign.objects.create(
            key='unclaimed-campaign',
            title='Unclaimed',
            assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
            duration_seconds=3600,
            event_key='unclaimed.event',
        )

        self.assertEqual(ActivityService.record_event(self.user, campaign.event_key, '1'), [])
        self.assertFalse(ActivityEvent.objects.filter(campaign=campaign).exists())

    def test_automatic_campaign_is_forced_for_new_space(self):
        campaign = ActivityCampaign.objects.create(
            key='automatic-campaign',
            title='Automatic',
            assignment_mode=ActivityCampaign.AssignmentMode.AUTOMATIC,
            duration_seconds=None,
            event_key='automatic.event',
        )

        ActivityService.ensure_automatic_for_space(self.space)

        run = SpaceActivity.objects.get(campaign=campaign, space=self.space)
        self.assertIsNone(run.ends_at)
        self.assertTrue(run.is_active())

    def test_ended_space_campaign_is_removed_from_active_feed(self):
        SpaceActivity.objects.filter(id=self.space_activity.id).update(ends_at=timezone.now() - timedelta(seconds=1))

        response = self.client.get('/activities/active', **self.authorization())

        self.assertNotIn(self.campaign.key, [item['key'] for item in response.json()['body']])

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

    def test_daily_event_resets_at_beijing_midnight(self):
        before_midnight = datetime(2026, 8, 26, 15, 30, tzinfo=datetime_timezone.utc)
        after_midnight = datetime(2026, 8, 26, 16, 30, tzinfo=datetime_timezone.utc)
        SpaceActivity.objects.filter(id=self.space_activity.id).update(
            claimed_at=before_midnight - timedelta(days=1),
        )

        with patch('Activity.models.timezone.now', return_value=before_midnight):
            ActivityService.record_event(self.user, 'square.statement.publish', 'before-midnight')
        with patch('Activity.models.timezone.now', return_value=after_midnight):
            ActivityService.record_event(self.user, 'square.statement.publish', 'after-midnight')

        events = ActivityEvent.objects.filter(campaign=self.campaign, progress__user=self.user).order_by('event_date')
        self.assertEqual(list(events.values_list('event_date', 'points')), [
            (before_midnight.astimezone(timezone.get_current_timezone()).date(), 1),
            (after_midnight.astimezone(timezone.get_current_timezone()).date(), 1),
        ])

    def test_friendly_neighbor_points_and_permanent_manual_rewards(self):
        campaign = ActivityCampaign.objects.create(
            key='friendly-neighbor-test',
            title='Friendly Neighbor',
            event_key='square.statement.comment',
            config={
                'mode': 'friendly_neighbor',
                'daily_points_limit': 25,
                'reply_points': [20, 10, 5],
                'user_rewards': [
                    {'key': 'frame', 'threshold': 75, 'resource_type': 'frame',
                     'reward_id': 'activity.spider.frame', 'resource_key': 'spider-web'},
                    {'key': 'profile', 'threshold': 100, 'resource_type': 'profile',
                     'reward_id': 'activity.spider.profile', 'resource_key': 'spider-city'},
                ],
            },
        )
        ActivityService.claim_for_space(campaign, self.space)

        self.assertEqual(ActivityService.record_friendly_neighbor_reply(self.user, 'comment-1'), 20)
        self.assertEqual(ActivityService.record_friendly_neighbor_reply(self.user, 'comment-2'), 5)
        self.assertEqual(ActivityService.record_friendly_neighbor_reply(self.user, 'comment-3'), 0)
        self.assertEqual(ActivityService.record_friendly_neighbor_reply(self.user, 'comment-1'), 0)
        progress = campaign.user_progress.get(user=self.user)
        self.assertEqual(progress.earned_points, 25)
        self.assertEqual(list(progress.events.order_by('id').values_list('points', flat=True)), [20, 5, 0])
        self.assertEqual(ActivityService.payload(campaign, self.user)['friendly_neighbor']['next_reply_points'], 0)

        progress.earned_points = 100
        progress.save(update_fields=['earned_points'])
        ActivityService.claim_milestone_reward(campaign, self.user, 'frame')
        ActivityService.claim_milestone_reward(campaign, self.user, 'frame')
        ActivityService.claim_milestone_reward(campaign, self.user, 'profile')
        payload = ActivityService.payload(campaign, self.user)['friendly_neighbor']
        self.assertTrue(all(reward['claimed'] for reward in payload['rewards']))
        self.assertEqual(UserResourceInventory.objects.filter(
            user=self.user, source='activity', source_reference=campaign.key,
        ).count(), 2)
        self.user.set_personalization(
            chat_bubble_style=self.user.chat_bubble_style,
            avatar_frame_style='spider-web',
            profile_card_theme='spider-city',
        )
        self.assertEqual(self.user.avatar_frame_style, 'spider-web')
        self.assertEqual(self.user.profile_card_theme, 'spider-city')
