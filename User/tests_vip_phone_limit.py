from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from User.models import PermanentVipCampaign, User, UserResourceInventory
from User.validators import UserErrors


class PermanentVipPhoneLimitTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='VIP A', slug='vip-a', email='a@example.com')
        self.other_space = Space.objects.create(name='VIP B', slug='vip-b', email='b@example.com')
        self.levels = {}
        self.level_patch = patch.object(User, 'effective_growth_level', lambda user: self.levels.get(user.pk, 6))
        self.level_patch.start()
        self.addCleanup(self.level_patch.stop)
        self.award_patch = patch.object(User, 'award_growth')
        self.award_patch.start()
        self.addCleanup(self.award_patch.stop)
        self.user = self.member(self.space, 'Candidate')

    def member(self, space, name, phone='+8613800138000'):
        user = User.create(space=space, name=name)
        user.phone = phone
        user.phone_verified_at = timezone.now()
        user.email_verified_at = timezone.now()
        user.save(update_fields=['phone', 'phone_verified_at', 'email_verified_at'])
        return user

    def vip(self, space=None, phone='+8613800138000', level=6):
        peer = self.member(space or self.other_space, 'Existing', phone)
        self.levels[peer.pk] = level
        UserResourceInventory.grant_permanent_vip_resources(peer, 1)
        return peer

    def test_cross_space_phone_blocks_reporting_and_claim(self):
        self.vip()
        status = PermanentVipCampaign.status_for(self.user)
        self.assertFalse(status['requirements']['phone_claim_available'])
        self.assertFalse(status['eligible'])
        with self.assertRaises(type(UserErrors.PERMANENT_VIP_PHONE_LIMIT)) as error:
            PermanentVipCampaign.claim_for(self.user)
        self.assertIs(error.exception, UserErrors.PERMANENT_VIP_PHONE_LIMIT)
        self.assertFalse(UserResourceInventory.objects.filter(user=self.user, reward_id='vip.permanent').exists())
        self.assertEqual(PermanentVipCampaign.objects.get().claimed_count, 0)

    def test_same_space_legacy_phone_and_flag_are_counted(self):
        peer = self.member(self.space, 'Legacy', '13800138000')
        peer.is_permanent_vip = True
        peer.save(update_fields=['is_permanent_vip'])
        self.assertFalse(PermanentVipCampaign.phone_claim_available(self.user))

    def test_lv9_candidate_is_exempt(self):
        self.vip()
        self.levels[self.user.pk] = 9
        self.assertTrue(PermanentVipCampaign.claim_for(self.user)['claimed_by_user'])

    def test_lv8_and_legacy_country_code_are_still_restricted(self):
        self.vip(phone='8613800138000')
        self.levels[self.user.pk] = 8
        self.assertFalse(PermanentVipCampaign.phone_claim_available(self.user))

    def test_lv9_still_requires_phone_verification(self):
        self.levels[self.user.pk] = 9
        self.user.phone_verified_at = None
        self.user.save(update_fields=['phone_verified_at'])
        with self.assertRaises(type(UserErrors.PERMANENT_VIP_NOT_ELIGIBLE)) as error:
            PermanentVipCampaign.claim_for(self.user)
        self.assertIs(error.exception, UserErrors.PERMANENT_VIP_NOT_ELIGIBLE)

    def test_lv9_existing_vip_does_not_consume_lower_level_quota(self):
        self.vip(level=9)
        self.assertTrue(PermanentVipCampaign.claim_for(self.user)['claimed_by_user'])

    def test_different_phone_can_claim(self):
        self.vip(phone='+8613800138001')
        self.assertTrue(PermanentVipCampaign.claim_for(self.user)['claimed_by_user'])

    def test_lv1_can_claim_at_every_former_slot_boundary(self):
        self.levels[self.user.pk] = 1
        for claimed in (0, 19, 20, 49, 50, 99):
            with self.subTest(claimed=claimed):
                PermanentVipCampaign.objects.update_or_create(
                    key='founding-100', defaults={'claimed_count': claimed},
                )
                status = PermanentVipCampaign.status_for(self.user)
                self.assertTrue(status['eligible'])
                self.assertEqual(status['required_level'], 1)
                self.assertEqual(status['level_thresholds'], [])
        self.assertTrue(PermanentVipCampaign.claim_for(self.user)['claimed_by_user'])

    def test_lv1_still_requires_email_and_phone(self):
        self.levels[self.user.pk] = 1
        self.user.email_verified_at = None
        self.user.save(update_fields=['email_verified_at'])
        self.assertFalse(PermanentVipCampaign.status_for(self.user)['eligible'])
        with self.assertRaises(type(UserErrors.PERMANENT_VIP_NOT_ELIGIBLE)):
            PermanentVipCampaign.claim_for(self.user)

    def test_stale_verified_snapshot_cannot_authorize_claim(self):
        User.objects.filter(pk=self.user.pk).update(email_verified_at=None)
        self.assertIsNotNone(self.user.email_verified_at)
        with self.assertRaises(type(UserErrors.PERMANENT_VIP_NOT_ELIGIBLE)):
            PermanentVipCampaign.claim_for(self.user)
        self.assertFalse(UserResourceInventory.objects.filter(user=self.user, reward_id='vip.permanent').exists())

    def test_new_account_with_real_lv1_calculation_can_claim(self):
        self.level_patch.stop()
        self.user.growth_score = 0
        self.user.save(update_fields=['growth_score'])
        self.assertEqual(self.user.effective_growth_level(), 1)
        self.assertTrue(PermanentVipCampaign.status_for(self.user)['eligible'])
        self.assertTrue(PermanentVipCampaign.claim_for(self.user)['claimed_by_user'])

    def test_lv1_duplicate_phone_is_still_blocked(self):
        self.levels[self.user.pk] = 1
        self.vip()
        self.assertFalse(PermanentVipCampaign.status_for(self.user)['eligible'])
        with self.assertRaises(type(UserErrors.PERMANENT_VIP_PHONE_LIMIT)):
            PermanentVipCampaign.claim_for(self.user)

    def test_existing_claim_remains_idempotent_even_with_legacy_duplicates(self):
        UserResourceInventory.grant_permanent_vip_resources(self.user, 2)
        self.vip()
        result = PermanentVipCampaign.claim_for(self.user)
        self.assertTrue(result['claimed_by_user'])
        self.assertEqual(result['slot'], 2)

    def test_second_claim_rechecks_after_first_account_claims(self):
        peer = self.member(self.other_space, 'Second')
        self.assertTrue(PermanentVipCampaign.status_for(peer)['eligible'])
        PermanentVipCampaign.claim_for(self.user)
        with self.assertRaises(type(UserErrors.PERMANENT_VIP_PHONE_LIMIT)):
            PermanentVipCampaign.claim_for(peer)
