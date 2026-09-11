from datetime import datetime, timedelta, timezone as datetime_timezone
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from Activity.models import ActivityAwakening, ActivityCampaign, ActivityEvent, ActivityMilestone, ActivityService, SpaceActivity, SpaceActivityReward, UserActivityReward
from Chat.models import Chat, ChatPurposeChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
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

    def test_starry_night_requires_claim_evening_window_and_consecutive_days(self):
        campaign = ActivityCampaign.objects.create(
            key='starry-night-test',
            title='Five Nights',
            assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
            duration_seconds=15 * 24 * 60 * 60,
            event_key='chat.message.send',
            config={
                'theme': 'starry-night',
                'mode': ActivityService.STARRY_NIGHT_MODE,
                'streak_days': 5,
                'reward': {
                    'resource_type': 'background',
                    'reward_id': 'activity.background.starry-night',
                    'resource_key': 'starry-night',
                },
            },
        )
        beijing_evening = datetime(2026, 9, 1, 12, 30, tzinfo=datetime_timezone.utc)

        self.assertEqual(ActivityService.record_starry_night_chat(self.user, 'before-claim', beijing_evening), [])
        self.assertFalse(campaign.events.exists())
        ActivityService.claim_for_space(campaign, self.space, claimed_at=beijing_evening - timedelta(hours=1))
        self.assertEqual(ActivityService.record_starry_night_chat(
            self.user, 'too-early', beijing_evening - timedelta(hours=1)), [])

        for offset in range(3):
            ActivityService.record_starry_night_chat(
                self.user, f'first-{offset}', beijing_evening + timedelta(days=offset))
        gap_day = beijing_evening + timedelta(days=4)
        ActivityService.record_starry_night_chat(self.user, 'after-gap', gap_day)
        progress = campaign.user_progress.get(user=self.user)
        self.assertEqual(ActivityService._consecutive_streak(progress, timezone.localtime(gap_day).date()), 1)

        for offset in range(5, 9):
            ActivityService.record_starry_night_chat(
                self.user, f'finish-{offset}', beijing_evening + timedelta(days=offset))

        self.assertTrue(UserResourceInventory.objects.filter(
            user=self.user,
            reward_id='activity.background.starry-night',
            resource_key='starry-night',
        ).exists())
        final_evening = beijing_evening + timedelta(days=8)
        with patch('Activity.models.timezone.now', return_value=final_evening):
            payload = ActivityService.payload(campaign, self.user)
        self.assertEqual(payload['starry_night']['streak_days'], 5)
        self.assertTrue(payload['starry_night']['reward_owned'])

    def test_starry_night_claim_backfills_previous_five_evenings(self):
        campaign = ActivityCampaign.objects.create(
            key='starry-night-backfill-test',
            title='Five Nights Backfill',
            assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
            duration_seconds=15 * 24 * 60 * 60,
            event_key='chat.message.send',
            config={
                'theme': 'starry-night',
                'mode': ActivityService.STARRY_NIGHT_MODE,
                'streak_days': 5,
                'reward': {
                    'resource_type': 'background',
                    'reward_id': 'activity.background.starry-night',
                    'resource_key': 'starry-night',
                },
            },
        )
        chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.DIRECT,
            created_by=self.user,
        )
        claimed_at = datetime(2026, 9, 6, 13, 0, tzinfo=datetime_timezone.utc)
        for offset in range(5):
            message = Message.objects.create(
                chat=chat,
                user=self.user,
                type=MessageTypeChoice.TEXT,
                content=f'night-{offset}',
            )
            Message.objects.filter(id=message.id).update(created_at=claimed_at - timedelta(days=4 - offset, minutes=15))

        run = ActivityService.claim_for_space(campaign, self.space, claimed_at=claimed_at)
        run.refresh_from_db()
        progress = campaign.user_progress.get(user=self.user)

        self.assertIsNotNone(run.history_backfilled_at)
        self.assertEqual(progress.events.count(), 5)
        self.assertEqual(progress.earned_points, 5)
        self.assertEqual(ActivityService.payload(campaign, self.user, run)['starry_night']['streak_days'], 5)
        self.assertTrue(UserResourceInventory.objects.filter(
            user=self.user,
            reward_id='activity.background.starry-night',
        ).exists())
        output = StringIO()
        call_command('backfill_starry_night_progress', campaign_key=campaign.key, stdout=output)
        self.assertEqual(progress.events.count(), 5)
        self.assertEqual(UserResourceInventory.objects.filter(
            user=self.user,
            reward_id='activity.background.starry-night',
        ).count(), 1)
        self.assertIn('events=0, rewards=0', output.getvalue())

    def test_starry_night_backfill_ignores_submission_and_preserves_yesterday_streak(self):
        campaign = ActivityCampaign.objects.create(
            key='starry-night-backfill-boundaries',
            title='Five Nights Boundaries',
            assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
            event_key='chat.message.send',
            config={'theme': 'starry-night', 'mode': ActivityService.STARRY_NIGHT_MODE, 'streak_days': 5},
        )
        ordinary = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.DIRECT, created_by=self.user)
        submission = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.DIRECT,
            purpose=ChatPurposeChoice.SUBMISSION,
            created_by=self.user,
        )
        claimed_at = datetime(2026, 9, 6, 11, 0, tzinfo=datetime_timezone.utc)
        for offset in range(2):
            message = Message.objects.create(chat=ordinary, user=self.user, type=MessageTypeChoice.TEXT, content='ordinary')
            Message.objects.filter(id=message.id).update(created_at=claimed_at - timedelta(days=2 - offset) + timedelta(hours=2))
        ignored = Message.objects.create(chat=submission, user=self.user, type=MessageTypeChoice.TEXT, content='submission')
        Message.objects.filter(id=ignored.id).update(created_at=claimed_at - timedelta(hours=22))

        run = ActivityService.claim_for_space(campaign, self.space, claimed_at=claimed_at)
        progress = campaign.user_progress.get(user=self.user)

        self.assertEqual(progress.events.count(), 2)
        self.assertEqual(ActivityService._starry_streak(progress, claimed_at.astimezone(ActivityService.BEIJING_TIMEZONE)), 2)
        with patch('Activity.models.timezone.now', return_value=claimed_at):
            self.assertEqual(ActivityService.payload(campaign, self.user, run)['starry_night']['streak_days'], 2)

    def test_starry_night_payload_backfills_an_existing_claim_once(self):
        campaign = ActivityCampaign.objects.create(
            key='starry-night-existing-claim',
            title='Existing Starry Claim',
            assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
            event_key='chat.message.send',
            config={'theme': 'starry-night', 'mode': ActivityService.STARRY_NIGHT_MODE, 'streak_days': 5},
        )
        chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.DIRECT, created_by=self.user)
        claimed_at = datetime(2026, 9, 6, 13, 0, tzinfo=datetime_timezone.utc)
        message = Message.objects.create(chat=chat, user=self.user, type=MessageTypeChoice.TEXT, content='before claim')
        Message.objects.filter(id=message.id).update(created_at=claimed_at - timedelta(minutes=15))
        run = SpaceActivity.objects.create(campaign=campaign, space=self.space, claimed_at=claimed_at)

        with patch('Activity.models.timezone.now', return_value=claimed_at):
            ActivityService.payload(campaign, self.user, run)
        run.refresh_from_db()
        ActivityService.payload(campaign, self.user, run)

        self.assertIsNotNone(run.history_backfilled_at)
        self.assertEqual(campaign.events.filter(progress__user=self.user).count(), 1)
