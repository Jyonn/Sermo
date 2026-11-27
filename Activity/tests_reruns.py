from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone
from smartdjango import Error

from Activity.models import (
    ActivityCampaign, ActivityMilestone, ActivityRerunRequest, ActivityRewardReceipt, ActivityService, SpaceActivity, UserActivityProgress,
)
from Activity.record_shop import claim_singer
from Activity.reruns import RerunErrors, activate_rerun, anniversary, apply_for_rerun, review_requests, rerun_info
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
        self.run = ActivityService.claim_for_space(self.campaign, self.space, timezone.now() - timedelta(days=210))

    def test_application_approval_and_activation_are_isolated_to_one_space(self):
        SpaceActivity.objects.filter(pk=self.run.pk).update(ends_at=timezone.now() - timedelta(days=120))
        other_run = ActivityService.claim_for_space(self.campaign, self.other)
        reader = bearer(auth.get_login_token(self.member))
        platform = bearer(auth.get_platform_admin_token('admin@example.test'))
        space = bearer(auth.get_space_login_token(self.space))
        path = f'/activities/admin/{self.campaign.key}/rerun'
        self.assertGreaterEqual(self.client.post(path + '/apply', **reader).status_code, 400)
        self.assertEqual(self.client.post(path + '/claim', **space).status_code, 403)
        self.assertEqual(self.client.post(path + '/apply', **space).status_code, 200)
        self.assertEqual(self.client.post(path + '/apply', **space).status_code, 200)
        request = ActivityRerunRequest.objects.get(previous_run=self.run)
        review_path = f'/platform-admin/activity-reruns/{request.pk}'
        data = {'approved': True, 'revision': request.submitted_at.isoformat()}
        for credentials in (reader, space, {}):
            self.assertGreaterEqual(self.client.post(review_path, data, content_type='application/json', **credentials).status_code, 400)
        result = self.client.post(review_path, data, content_type='application/json', **platform)
        self.assertEqual(result.status_code, 200, result.content.decode())
        self.assertEqual(PlatformAuditLog.objects.get(action='activity.rerun.review').metadata['space_id'], self.space.pk)
        self.assertFalse(self.campaign.reruns.exists())
        claim = self.client.post(path + '/claim', **space)
        self.assertEqual(claim.status_code, 200, claim.content.decode())
        new = self.campaign.reruns.get()
        self.assertEqual((new.edition, new.assignment_mode, new.duration_seconds), (2, 'manual', 86400))
        catalog = self.client.get('/activities/active', **reader).json()['body']
        self.assertTrue(any(item['edition'] == 2 and item['original_key'] == self.campaign.key for item in catalog))
        active = new.space_runs.get()
        self.assertEqual(activate_rerun(self.campaign.key, self.space).pk, active.pk)
        self.assertEqual(active.ends_at - active.claimed_at, timedelta(days=1))
        self.assertEqual(self.client.post(f'/activities/admin/{new.key}/claim', **bearer(auth.get_space_login_token(self.other))).status_code, 403)
        self.assertTrue(other_run.is_active())
        self.assertFalse(new.space_runs.filter(space=self.other).exists())

    def test_anniversaries_clamp_to_end_of_month_and_auto_open_at_midnight(self):
        zone = ZoneInfo('Asia/Shanghai')
        end = datetime(2026, 8, 31, 18, 35, tzinfo=zone)
        self.run.ends_at = end
        apply = datetime(2026, 11, 30, tzinfo=zone)
        auto = datetime(2027, 2, 28, tzinfo=zone)
        self.assertEqual(anniversary(end, 3), apply)
        self.assertEqual(anniversary(end, 6), auto)
        self.assertEqual(rerun_info(self.run, apply - timedelta(seconds=1))['status'], 'waiting')
        self.assertEqual(rerun_info(self.run, apply)['status'], 'eligible')
        self.assertEqual(rerun_info(self.run, auto - timedelta(seconds=1))['status'], 'eligible')
        self.assertEqual(rerun_info(self.run, auto)['status'], 'ready')

    def test_rejects_permanent_or_unfinished_and_uses_own_edition_number(self):
        for key, config in (('permanent-fixture', {}), ('one-off-fixture', {'mode': 'annual_explorer'})):
            campaign = ActivityCampaign.objects.create(key=key, title=key, event_key='test.only',
                                                       duration_seconds=86400 if config else None, config=config)
            SpaceActivity.objects.create(
                campaign=campaign, space=self.other, claimed_at=timezone.now() - timedelta(days=230),
                ends_at=timezone.now() - timedelta(days=229) if config else None,
            )
            with self.assertRaises(Error) as blocked:
                activate_rerun(campaign.key, self.other)
            self.assertIs(blocked.exception, RerunErrors.UNAVAILABLE)
        other_run = ActivityService.claim_for_space(self.campaign, self.other)
        with self.assertRaises(Error) as blocked:
            activate_rerun(self.campaign.key, self.other)
        self.assertIs(blocked.exception, RerunErrors.UNAVAILABLE)
        second = activate_rerun(self.campaign.key, self.space)
        self.assertFalse(ActivityRerunRequest.objects.exists())
        SpaceActivity.objects.filter(pk=second.pk).update(ends_at=timezone.now() - timedelta(days=220))
        third = activate_rerun(second.campaign.key, self.space).campaign
        self.assertEqual((third.original_id, third.edition), (self.campaign.pk, 3))
        SpaceActivity.objects.filter(pk=other_run.pk).update(ends_at=timezone.now() - timedelta(days=210))
        self.assertEqual(activate_rerun(self.campaign.key, self.other).campaign_id, second.campaign_id)

    def test_rejected_or_pending_approval_does_not_prevent_automatic_opening(self):
        SpaceActivity.objects.filter(pk=self.run.pk).update(ends_at=timezone.now() - timedelta(days=120))
        apply_for_rerun(self.campaign.key, self.space)
        platform = bearer(auth.get_platform_admin_token('admin@example.test'))
        request = ActivityRerunRequest.objects.get(previous_run=self.run)
        path = f'/platform-admin/activity-reruns/{request.pk}'
        stale = request.submitted_at.isoformat()
        self.assertEqual(self.client.post(path, {'approved': False, 'revision': stale},
                                          content_type='application/json', **platform).status_code, 200)
        apply_for_rerun(self.campaign.key, self.space)
        self.assertEqual(self.client.post(path, {'approved': True, 'revision': stale},
                                          content_type='application/json', **platform).status_code, 409)
        SpaceActivity.objects.filter(pk=self.run.pk).update(ends_at=timezone.now() - timedelta(days=220))
        self.assertEqual(review_requests()[0]['status'], 'closed')
        self.assertTrue(activate_rerun(self.campaign.key, self.space).is_active())

    def test_progress_restarts_and_duplicate_reward_credits_once(self):
        old_progress = UserActivityProgress.objects.create(
            campaign=self.campaign, space_activity=self.run, user=self.member, earned_points=77,
        )
        UserResourceInventory.grant_activity_resource(self.member, 'bubble', 'prior.alias', 'test-bubble', self.campaign.key)
        new = activate_rerun(self.campaign.key, self.space).campaign
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
        run = activate_rerun(self.campaign.key, self.space)
        next_campaign = run.campaign
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
        SpaceActivity.objects.filter(pk=past.pk).update(ends_at=timezone.now() - timedelta(days=220))
        current = activate_rerun(music.key, self.space)
        music_next = current.campaign
        UserActivityProgress.objects.create(campaign=music_next, user=self.member, space_activity=current, earned_points=30)
        for _ in range(2):
            self.assertEqual(claim_singer(music_next.key, self.member, 'jay')['rerun_growth'], 10)
        self.assertEqual(ActivityRewardReceipt.objects.filter(campaign=music_next, user=self.member).count(), 1)

    def test_starry_night_rerun_does_not_backfill_messages(self):
        self.campaign.config = {'mode': ActivityService.STARRY_NIGHT_MODE}
        self.campaign.save(update_fields=['config'])
        with patch.object(ActivityService, 'backfill_starry_night_progress') as backfill:
            run = activate_rerun(self.campaign.key, self.space)
            payload = ActivityService.payload(run.campaign, self.member, run)
            backfill.assert_not_called()
        self.assertEqual(payload['starry_night']['streak_days'], 0)
