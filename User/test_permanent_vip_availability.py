from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from smartdjango import Error

from Activity.models import ActivityCampaign, ActivityService, SpaceActivity
from Space.models import Space
from User.growth import PERMANENT_VIP_RESOURCES
from User.models import GrowthEvent, PermanentVipCampaign, User, UserResourceInventory
from User.validators import UserErrors


class PermanentVipAvailabilityTests(TestCase):
    def setUp(self):
        ActivityCampaign.objects.filter(key=PermanentVipCampaign.ACTIVITY_KEY).delete()
        self.space = Space.objects.create(name='VIP fixture', slug='vip-fixture', email='admin@example.com',
                                          group_square_enabled=True, admin_phone_verified_at=timezone.now())
        self.user = User.create(self.space, 'Member', email='member@example.com', verified=True)
        self.user.email_verified_at = timezone.now()
        self.user.phone = '+8613800138000'
        self.user.phone_verified_at = timezone.now()
        self.user.save(update_fields=['email_verified_at', 'phone', 'phone_verified_at'])
        self.level_patch = patch.object(User, 'effective_growth_level', return_value=6)
        self.level = self.level_patch.start()
        self.addCleanup(self.level_patch.stop)

    def campaign(self, automatic=False, enabled=True):
        return ActivityCampaign.objects.create(
            key=PermanentVipCampaign.ACTIVITY_KEY, title='VIP', enabled=enabled,
            assignment_mode=ActivityCampaign.AssignmentMode.AUTOMATIC if automatic else ActivityCampaign.AssignmentMode.MANUAL,
            config={'theme': 'permanent-vip'},
        )

    def assert_closed_without_award(self):
        self.assertFalse(PermanentVipCampaign.status_for(self.user)['space_activity_active'])
        with self.assertRaises(Error) as caught:
            PermanentVipCampaign.claim_for(self.user)
        self.assertEqual(caught.exception, UserErrors.PERMANENT_VIP_CAMPAIGN_INACTIVE)
        self.assertEqual(PermanentVipCampaign.objects.get(key='founding-100').claimed_count, 0)
        self.assertFalse(UserResourceInventory.objects.filter(user=self.user, reward_id='vip.permanent').exists())
        self.assertFalse(GrowthEvent.objects.filter(user=self.user, event_key='vip:permanent').exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_permanent_vip)

    def test_missing_campaign_is_closed(self):
        self.assert_closed_without_award()

    def test_disabled_campaign_is_closed_despite_active_assignment(self):
        campaign = self.campaign(enabled=False)
        ActivityService.claim_for_space(campaign, self.space)
        self.assert_closed_without_award()

    def test_unassigned_manual_campaign_is_closed_instead_of_raising_lookup_error(self):
        self.campaign()
        self.assert_closed_without_award()

    def test_future_assignment_is_closed(self):
        campaign = self.campaign()
        ActivityService.claim_for_space(campaign, self.space, claimed_at=timezone.now() + timedelta(days=1))
        self.assert_closed_without_award()

    def test_expired_assignment_is_closed(self):
        campaign = self.campaign()
        run = ActivityService.claim_for_space(campaign, self.space)
        run.ends_at = timezone.now() - timedelta(seconds=1)
        run.save(update_fields=['ends_at'])
        self.assert_closed_without_award()

    def test_other_space_assignment_does_not_authorize_this_user(self):
        campaign = self.campaign()
        other = Space.objects.create(name='Other', slug='vip-other', email='other@example.com',
                                     group_square_enabled=True, admin_phone_verified_at=timezone.now())
        ActivityService.claim_for_space(campaign, other)
        self.assert_closed_without_award()

    def test_automatic_policy_creates_only_its_own_assignment(self):
        campaign = self.campaign(automatic=True)
        unrelated = ActivityCampaign.objects.create(key='vip-unrelated', title='Unrelated', assignment_mode='automatic')
        status = PermanentVipCampaign.status_for(self.user)
        self.assertTrue(status['space_activity_active'])
        self.assertTrue(SpaceActivity.objects.filter(campaign=campaign, space=self.space).exists())
        self.assertFalse(SpaceActivity.objects.filter(campaign=unrelated, space=self.space).exists())
        receipt = PermanentVipCampaign.claim_for(self.user)
        self.assertTrue(receipt['claimed_by_user'])
        self.assertEqual(receipt['slot'], 1)

    def test_active_claim_and_retry_grant_once(self):
        ActivityService.claim_for_space(self.campaign(), self.space)
        first = PermanentVipCampaign.claim_for(self.user)
        second = PermanentVipCampaign.claim_for(self.user)
        self.assertEqual((first['slot'], second['slot']), (1, 1))
        self.assertEqual(PermanentVipCampaign.objects.get(key='founding-100').claimed_count, 1)
        for _, reward_id, _ in PERMANENT_VIP_RESOURCES:
            self.assertEqual(UserResourceInventory.objects.filter(user=self.user, reward_id=reward_id).count(), 1)
        self.assertEqual(list(GrowthEvent.objects.filter(user=self.user, event_key='vip:permanent').values_list('points', flat=True)), [500])

    def test_shared_availability_preserves_current_participation_metadata(self):
        ActivityService.claim_for_space(self.campaign(), self.space)
        self.assertEqual(PermanentVipCampaign.status_for(self.user)['participation'], dict(count=0, users=[]))
        result = PermanentVipCampaign.claim_for(self.user)
        self.assertEqual(result['participation']['count'], 1)
        self.assertEqual(result['participation']['users'][0]['user_id'], self.user.pk)

    def test_claim_reads_current_binding_instead_of_stale_user_object(self):
        ActivityService.claim_for_space(self.campaign(), self.space)
        User.objects.filter(pk=self.user.pk).update(email_verified_at=None)
        with self.assertRaises(Error) as caught:
            PermanentVipCampaign.claim_for(self.user)
        self.assertEqual(caught.exception, UserErrors.PERMANENT_VIP_NOT_ELIGIBLE)
        self.assertFalse(UserResourceInventory.objects.filter(user=self.user, reward_id='vip.permanent').exists())

    def test_status_and_claim_share_no_minimum_level_policy(self):
        ActivityService.claim_for_space(self.campaign(), self.space)
        PermanentVipCampaign.objects.update_or_create(key='founding-100', defaults={'claimed_count': 20})
        self.level.return_value = 1
        status = PermanentVipCampaign.status_for(self.user)
        self.assertEqual(status['required_level'], 1)
        self.assertEqual(status['level_thresholds'], [])
        self.assertTrue(status['eligible'])
        self.assertEqual(PermanentVipCampaign.claim_for(self.user)['slot'], 21)

    def test_growth_failure_rolls_back_slot_inventory_and_vip_flag(self):
        ActivityService.claim_for_space(self.campaign(), self.space)
        PermanentVipCampaign.objects.get_or_create(key='founding-100')
        with patch.object(User, 'award_growth', side_effect=RuntimeError('fixture growth failure')):
            with self.assertRaises(RuntimeError):
                PermanentVipCampaign.claim_for(self.user)
        self.assertEqual(PermanentVipCampaign.objects.get(key='founding-100').claimed_count, 0)
        self.assertFalse(UserResourceInventory.objects.filter(user=self.user, reward_id='vip.permanent').exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_permanent_vip)

    def test_owned_legacy_claim_can_repair_bundle_without_open_activity_or_new_slot(self):
        UserResourceInventory.grant_permanent_vip_resources(self.user, 7)
        UserResourceInventory.objects.filter(user=self.user).exclude(reward_id='vip.permanent').delete()
        receipt = PermanentVipCampaign.claim_for(self.user)
        self.assertTrue(receipt['claimed_by_user'])
        self.assertFalse(receipt['active'])
        self.assertEqual((receipt['slot'], receipt['claimed']), (7, 0))
        for _, reward_id, _ in PERMANENT_VIP_RESOURCES:
            self.assertTrue(UserResourceInventory.objects.filter(user=self.user, reward_id=reward_id).exists())

    def test_endpoint_returns_closed_status_and_denies_direct_post(self):
        from utils import auth

        self.campaign()
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(self.user)['auth']}
        response = self.client.get('/users/me/permanent-vip', **headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['body']['space_activity_active'])
        denied = self.client.post('/users/me/permanent-vip', **headers)
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(UserResourceInventory.objects.filter(user=self.user, reward_id='vip.permanent').exists())
