from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from smartdjango import Error

from Activity.models import (
    ActivityCampaign, ActivityMilestone, ActivityRewardReceipt, ActivityService, SpaceActivity, UserActivityProgress,
)
from Activity.record_shop import claim_singer
from Activity.reruns import RerunErrors, platform_campaigns, publish_rerun
from PlatformAdmin.models import PlatformAuditLog
from Space.models import Space
from User.models import User, UserResourceInventory
from utils import auth


def bearer(token):
    return {'HTTP_AUTHORIZATION': f"Bearer {token['auth']}"}


class ActivityRerunTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(slug='edition-space', name='Edition space', email='edition@example.test')
        self.other = Space.objects.create(slug='edition-other', name='Other space', email='other@example.test')
        self.member = User.create(self.space, 'Returning member', verified=True)
        self.newcomer = User.create(self.space, 'New member', verified=True)
        self.campaign = ActivityCampaign.objects.create(
            key='edition-archive', title='Archived activity', event_key='test.edition', duration_seconds=86400,
            config={'personal_event_target': 1, 'personal_reward_pool': [
                {'reward_id': 'example.bubble', 'resource_key': 'test-bubble', 'resource_type': 'bubble'},
            ]},
        )
        self.run = ActivityService.claim_for_space(self.campaign, self.space, timezone.now() - timedelta(days=5))

    def test_release_requires_platform_admin_and_space_activation(self):
        reader = bearer(auth.get_login_token(self.member))
        platform = bearer(auth.get_platform_admin_token('admin@example.test'))
        space = bearer(auth.get_space_login_token(self.space))
        path = f'/platform-admin/activities/{self.campaign.key}/rerun'
        for credentials in (reader, space, {}):
            self.assertGreaterEqual(self.client.post(path, **credentials).status_code, 400)
        self.assertEqual(self.campaign.reruns.count(), 0)
        result = self.client.post(path, **platform)
        self.assertEqual(result.status_code, 200, result.content.decode())
        new = self.campaign.reruns.get()
        self.assertEqual((new.edition, new.assignment_mode, new.duration_seconds), (2, 'manual', 86400))
        self.assertEqual(new.space_runs.count(), 0)
        self.assertEqual(PlatformAuditLog.objects.get(action='activity.rerun').target_id, new.pk)
        self.assertEqual(self.client.post(path, **platform).status_code, 409)
        self.assertEqual(self.client.get(f'/activities/{new.key}', **reader).status_code, 403)
        self.assertGreaterEqual(self.client.post(f'/activities/admin/{new.key}/claim', **reader).status_code, 400)
        claim = self.client.post(f'/activities/admin/{new.key}/claim', **space)
        self.assertEqual(claim.status_code, 200, claim.content.decode())
        catalog = self.client.get('/activities/active', **reader).json()['body']
        self.assertTrue(any(item['edition'] == 2 and item['original_key'] == self.campaign.key for item in catalog))
        active = new.space_runs.get()
        self.assertEqual(ActivityService.claim_for_space(new, self.space).pk, active.pk)
        self.assertEqual(active.ends_at - active.claimed_at, timedelta(days=1))
        self.assertNotIn(self.campaign.key, [item['key'] for item in ActivityService.admin_payloads(self.other)])
        with self.assertRaises(Error) as superseded:
            ActivityService.claim_for_space(self.campaign, self.other)
        self.assertIs(superseded.exception, RerunErrors.SUPERSEDED)

    def test_rejects_permanent_or_unfinished_and_numbers_next_edition(self):
        for key, config in (('permanent-fixture', {}), ('one-off-fixture', {'mode': 'annual_explorer'})):
            campaign = ActivityCampaign.objects.create(key=key, title=key, event_key='test.only', config=config)
            with self.assertRaises(Error) as blocked:
                publish_rerun(campaign.key)
            self.assertIs(blocked.exception, RerunErrors.UNAVAILABLE)
        other_run = ActivityService.claim_for_space(self.campaign, self.other)
        with self.assertRaises(Error) as blocked:
            publish_rerun(self.campaign.key)
        self.assertIs(blocked.exception, RerunErrors.UNAVAILABLE)
        SpaceActivity.objects.filter(pk=other_run.pk).update(ends_at=timezone.now())
        edition = publish_rerun(self.campaign.key)
        with self.assertRaises(Error) as blocked:
            publish_rerun(edition.key)
        self.assertIs(blocked.exception, RerunErrors.UNAVAILABLE)
        ActivityService.claim_for_space(edition, self.space, timezone.now() - timedelta(days=2))
        third = publish_rerun(edition.key)
        self.assertEqual((third.original_id, third.edition), (self.campaign.pk, 3))
        item = next(item for item in platform_campaigns() if item['key'] == third.key)
        self.assertEqual([row['edition'] for row in item['previous_editions']], [2, 1])

    def test_progress_restarts_and_duplicate_reward_credits_once(self):
        old_progress = UserActivityProgress.objects.create(
            campaign=self.campaign, space_activity=self.run, user=self.member, earned_points=77,
        )
        UserResourceInventory.grant_activity_resource(self.member, 'bubble', 'prior.alias', 'test-bubble', self.campaign.key)
        new = publish_rerun(self.campaign.key)
        ActivityService.claim_for_space(new, self.space)
        payload = ActivityService.payload(new, self.member)
        self.assertEqual(payload['personal_event_count'], 0)
        self.assertFalse(payload['personal_reward_claimable'])
        for user in (self.member, self.newcomer):
            ActivityService.record_event(user, new.event_key, 'new-task')
            ActivityService.claim_personal_reward(new, user)
            ActivityService.claim_personal_reward(new, user)
        old_progress.refresh_from_db()
        self.assertEqual(old_progress.earned_points, 77)
        self.assertEqual(ActivityService.payload(new, self.member)['rerun_growth'], 10)
        self.assertEqual(ActivityService.payload(new, self.newcomer)['rerun_growth'], 0)
        self.assertTrue(self.newcomer.resource_inventory.filter(reward_id='example.bubble').exists())
        self.assertFalse(self.member.resource_inventory.filter(reward_id='example.bubble').exists())
        self.assertEqual(list(self.member.growth_events.filter(event_key__startswith='activity:rerun').values_list('points', flat=True)), [10])
        self.member.reconcile_growth()
        self.assertEqual(self.member.growth_events.filter(event_key__startswith='activity:rerun').get().points, 10)

    def test_shared_milestone_and_singer_claims_keep_same_reward_identity(self):
        ActivityMilestone.objects.create(
            campaign=self.campaign, threshold=1, resource_type='frame',
            reward_pool=[{'reward_id': 'test.frame', 'resource_key': 'rerun-frame'}],
        )
        UserResourceInventory.grant_activity_resource(self.member, 'frame', 'test.frame', 'rerun-frame', self.campaign.key)
        next_campaign = publish_rerun(self.campaign.key)
        run = ActivityService.claim_for_space(next_campaign, self.space)
        run.total_points = 1
        run.save(update_fields=['total_points'])
        ActivityService.claim_space_reward(next_campaign, self.newcomer)
        self.assertEqual(ActivityService.payload(next_campaign, self.member)['rerun_growth'], 10)
        self.assertEqual(ActivityService.payload(next_campaign, self.member)['rerun_growth'], 10)
        self.assertTrue(self.newcomer.resource_inventory.filter(reward_id='test.frame').exists())
        music = ActivityCampaign.objects.create(key='edition-music', title='Music', event_key='edition.music',
                                                duration_seconds=86400, config={'mode': 'record_shop'})
        past = ActivityService.claim_for_space(music, self.space, timezone.now() - timedelta(hours=1))
        UserActivityProgress.objects.create(campaign=music, user=self.member, space_activity=past, earned_points=30)
        claim_singer(music.key, self.member, 'jay')
        SpaceActivity.objects.filter(pk=past.pk).update(ends_at=timezone.now())
        music_next = publish_rerun(music.key)
        current = ActivityService.claim_for_space(music_next, self.space)
        UserActivityProgress.objects.create(campaign=music_next, user=self.member, space_activity=current, earned_points=30)
        for _ in range(2):
            self.assertEqual(claim_singer(music_next.key, self.member, 'jay')['rerun_growth'], 10)
        self.assertEqual(ActivityRewardReceipt.objects.filter(campaign=music_next, user=self.member).count(), 1)

    def test_starry_night_rerun_does_not_backfill_messages(self):
        self.campaign.config = {'mode': ActivityService.STARRY_NIGHT_MODE}
        self.campaign.save(update_fields=['config'])
        new = publish_rerun(self.campaign.key)
        with patch.object(ActivityService, 'backfill_starry_night_progress') as backfill:
            run = ActivityService.claim_for_space(new, self.space)
            payload = ActivityService.payload(new, self.member, run)
            backfill.assert_not_called()
        self.assertEqual(payload['starry_night']['streak_days'], 0)
