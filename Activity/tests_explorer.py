from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase

from Activity.explorer import ExplorerService
from Activity.models import ActivityCampaign, ActivityService, BgmParticipation, SpaceActivity, UserActivityProgress
from Chat.models import Chat, ChatPurposeChoice, ChatTypeChoice, Submission
from Space.models import Space
from Square.models import Statement
from TravelMap.models import MapCheckIn
from User.models import User, UserErrors, UserResourceInventory
from Message.models import MediaAsset, MediaResource, MessageTypeChoice, WelcomeMessageTemplate
from utils import auth


class ExplorerTests(TestCase):
    def setUp(self):
        self.clock = patch('Activity.explorer.timezone.now', return_value=ExplorerService.START+timedelta(hours=12))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.binding = patch.object(ExplorerService, 'chronelle_bound', return_value=False)
        self.bound = self.binding.start();self.addCleanup(self.binding.stop)
        self.space = Space.objects.create(name='Explorer', slug='explorer-test', email='admin@example.com')
        self.user = User.create(self.space, 'Detective', email='detective@example.com', verified=True)
        self.campaign = ActivityCampaign.objects.get(key='explorer-2026')
        self.run = ActivityService.claim_for_space(self.campaign, self.space)

    def payload(self):
        self.user.refresh_from_db()
        return ExplorerService.payload(self.campaign, self.user, self.run)['explorer']

    def claim(self, tier):
        return self.client.post(f'/activities/{self.campaign.key}/explorer/rewards/{tier}/claim',
                                HTTP_AUTHORIZATION='Bearer '+auth.get_login_token(self.user)['auth'])

    def basic(self):
        with patch.object(User, 'require_capability', return_value=self.user):
            WelcomeMessageTemplate.replace_for(self.user, [{'type': MessageTypeChoice.TEXT, 'content': '欢迎来到我的侦探事务所。'}])
        self.user.signature='真相只有一个。'
        self.user.save(update_fields=['signature'])

    def test_welcome_uses_templates_and_ignores_archived_text(self):
        self.user.welcome_message = 'An obsolete custom greeting'
        self.user.save(update_fields=['welcome_message'])
        self.assertFalse(ExplorerService.evidence(self.user, self.campaign)['basic'][0][1])
        self.basic()
        self.assertTrue(ExplorerService.evidence(self.user, self.campaign)['basic'][0][1])

    def test_every_supported_non_text_welcome_counts(self):
        from Sticker.models import StickerAsset
        import json

        for kind in WelcomeMessageTemplate.ALLOWED_TYPES - {MessageTypeChoice.TEXT}:
            with self.subTest(kind=kind):
                if kind == MessageTypeChoice.STICKER:
                    sticker = StickerAsset.objects.create(content_hash='f' * 64, storage_key='welcome/sticker.webp')
                    item = {'type': kind, 'content': json.dumps({'asset_id': sticker.id})}
                else:
                    from Message.models import Message
                    media_kind = MediaAsset.kind_for_name(Message.MEDIA_KIND_BY_TYPE[kind])
                    asset = MediaAsset.objects.create(source_key=f'welcome/{kind}', source_uri=f'https://example.test/{kind}',
                                                     kind=media_kind, status=MediaAsset.STATUS_READY)
                    resource = MediaResource.acquire(self.user, asset, media_kind)
                    item = {'type': kind, 'resource_id': resource.id}
                with patch.object(User, 'require_capability', return_value=self.user):
                    WelcomeMessageTemplate.replace_for(self.user, [item])
                self.assertTrue(ExplorerService.evidence(self.user, self.campaign)['basic'][0][1])
                self.user.ensure_welcome_messages()
                self.assertEqual(self.user.welcome_message_templates.get().type, kind)
                self.assertEqual(self.user._dictify_welcome_message(), '')

    def test_default_welcome_in_either_language_and_empty_lists_do_not_count(self):
        template = self.user.welcome_message_templates.get()
        for language in ('zh-CN', 'en'):
            template.content = User.default_welcome_message(self.space, self.user.role, language)
            template.save(update_fields=['content'])
            self.assertFalse(ExplorerService.evidence(self.user, self.campaign)['basic'][0][1])
        template.delete()
        self.assertFalse(ExplorerService.evidence(self.user, self.campaign)['basic'][0][1])

    def security(self):
        self.user.phone='13800000001';self.user.phone_verified_at=ExplorerService.START
        self.user.save(update_fields=['phone','phone_verified_at'])

    def test_fixed_window_does_not_extend_for_new_spaces(self):
        with patch('Activity.models.timezone.now', return_value=ExplorerService.END-timedelta(days=1)):
            other=Space.objects.create(name='Late',slug='explorer-late',email='late@example.com')
            run=ActivityService.claim_for_space(self.campaign,other)
        self.assertEqual(run.claimed_at,ExplorerService.START)
        self.assertEqual(run.ends_at,ExplorerService.END)
        self.assertEqual(self.payload()['completed_count'],0)

    def test_defaults_do_not_count_and_themes_need_all_checks(self):
        self.user.signature='A custom signature';self.user.save(update_fields=['signature'])
        self.assertFalse(self.payload()['themes'][0]['completed'])
        self.basic();self.assertTrue(self.payload()['themes'][0]['completed'])
        self.user.signature='';self.user.save(update_fields=['signature'])
        self.assertTrue(self.payload()['themes'][0]['completed'])

    def test_security_requires_verified_phone_and_email(self):
        self.user.phone='13800000001';self.user.save(update_fields=['phone'])
        self.assertFalse(self.payload()['themes'][1]['completed'])
        self.security();self.assertTrue(self.payload()['themes'][1]['completed'])

    def test_application_theme_needs_both_native_binding_and_checkin(self):
        self.bound.return_value=True
        self.assertFalse(self.payload()['themes'][2]['completed'])
        MapCheckIn.objects.create(user=self.user,region_code='CN-3301',region_name='Hangzhou',country_code='CN',country_name='China')
        self.assertTrue(self.payload()['themes'][2]['completed'])

    def test_only_in_window_own_space_statements_count(self):
        statement=Statement.objects.create(user=self.user,space=self.space,text='Before launch')
        Statement.objects.filter(pk=statement.pk).update(created_at=ExplorerService.START-timedelta(seconds=1))
        self.assertFalse(self.payload()['themes'][3]['completed'])
        Statement.objects.filter(pk=statement.pk).update(created_at=ExplorerService.END)
        self.assertFalse(self.payload()['themes'][3]['completed'])
        Statement.objects.filter(pk=statement.pk).update(created_at=ExplorerService.START)
        self.assertTrue(self.payload()['themes'][3]['completed'])

    def test_submitted_entry_counts_but_draft_does_not(self):
        chat=Chat.objects.create(space=self.space,created_by=self.user,chat_type=ChatTypeChoice.GROUP,purpose=ChatPurposeChoice.SUBMISSION,title='Submission')
        submission=Submission.objects.create(chat=chat,author=self.user,recipient=self.user,client_draft_id='explorer-test')
        self.assertFalse(self.payload()['themes'][3]['completed'])
        submission.submitted_at=ExplorerService.START;submission.save(update_fields=['submitted_at'])
        self.assertTrue(self.payload()['themes'][3]['completed'])

    def test_other_activity_requires_every_reward_not_just_points(self):
        other=ActivityCampaign.objects.create(key='explorer-other',title='Other',config={'mode':'friendly_neighbor','user_rewards':[{'reward_id':'other.one'},{'reward_id':'other.two'}]})
        run=ActivityService.claim_for_space(other,self.space)
        UserActivityProgress.objects.create(user=self.user,campaign=other,space_activity=run,earned_points=1000)
        self.assertFalse(self.payload()['themes'][4]['completed'])
        UserResourceInventory.grant_activity_resource(self.user,'frame','other.one','one',other.key)
        self.assertFalse(self.payload()['themes'][4]['completed'])
        UserResourceInventory.grant_activity_resource(self.user,'profile','other.two','two',other.key)
        self.assertTrue(self.payload()['themes'][4]['completed'])

    def test_themes_complete_in_any_order_but_rewards_claim_sequentially_and_once(self):
        self.security()
        data=self.payload();self.assertEqual(data['completed_count'],1)
        self.assertTrue(data['rewards'][0]['claimable'])
        self.assertEqual(self.claim(2).status_code,403)
        self.assertEqual(self.claim(1).status_code,200)
        self.assertEqual(self.claim(1).status_code,200)
        self.assertEqual(self.user.resource_inventory.filter(reward_id=ExplorerService.REWARDS[0][1]).count(),1)
        self.basic();self.assertEqual(self.claim(2).status_code,200)
        self.assertFalse(self.payload()['rewards'][2]['unlocked'])

    def test_early_or_late_requests_cannot_create_progress_or_claim(self):
        self.basic()
        for now in [ExplorerService.START-timedelta(seconds=1),ExplorerService.END]:
            with patch('Activity.explorer.timezone.now',return_value=now):
                self.assertEqual(self.payload()['completed_count'],0)
                self.assertEqual(self.claim(1).status_code,403)

    def test_cross_space_run_and_forged_tier_are_rejected(self):
        other=Space.objects.create(name='Other',slug='explorer-other-space',email='other@example.com')
        run=ActivityService.claim_for_space(self.campaign,other)
        with self.assertRaises(Exception):ExplorerService.payload(self.campaign,self.user,run)
        self.assertEqual(self.claim(6).status_code,400)

    def test_reward_inventory_and_badge_survive_end_of_activity(self):
        with patch.object(ExplorerService,'evidence',return_value={key:[('fixture',True)] for key in ExplorerService.THEMES}):
            for tier in range(1,6):self.assertEqual(self.claim(tier).status_code,200)
        self.assertEqual(self.user.explorer_badge_years(),[2026])
        self.assertEqual(self.user.json_friend()['explorer_badge_years'],[2026])
        with patch('Activity.explorer.timezone.now',return_value=ExplorerService.END+timedelta(days=1)):
            self.assertTrue(all(item['claimed'] for item in self.payload()['rewards']))
            self.assertEqual(self.user.resource_inventory.filter(source_reference=self.campaign.key).count(),5)

    def test_generic_point_claim_cannot_bypass_exploration(self):
        response=self.client.post(f'/activities/{self.campaign.key}/claim',HTTP_AUTHORIZATION='Bearer '+auth.get_login_token(self.user)['auth'])
        self.assertNotEqual(response.status_code,200)
        self.assertFalse(self.user.resource_inventory.filter(source_reference=self.campaign.key).exists())

    def test_all_four_wearables_require_claimed_inventory(self):
        attempts = [
            lambda: self.user.set_personalization(chat_bubble_style='explorer-ai', avatar_frame_style='none'),
            lambda: self.user.set_personalization(chat_bubble_style='default', avatar_frame_style='explorer-conan'),
            lambda: self.user.set_chat_background('explorer-duality'),
            lambda: self.user.set_personalization(chat_bubble_style='default', avatar_frame_style='none', profile_card_theme='explorer-romance'),
        ]
        for attempt in attempts:
            self.user.refresh_from_db()
            with self.assertRaises(UserErrors.PERSONALIZATION_NOT_OWNED.__class__):
                attempt()
        for kind, reward_id, key, rarity in ExplorerService.REWARDS[:4]:
            UserResourceInventory.grant_activity_resource(self.user, kind, reward_id, key, self.campaign.key)
        for attempt in attempts:
            self.user.refresh_from_db()
            attempt()
        self.user.refresh_from_db()
        self.assertEqual(self.user.chat_background_theme, 'explorer-duality')
        self.assertEqual(self.user.profile_card_theme, 'explorer-romance')

    def test_vip_requires_every_permanent_reward(self):
        from User.growth import PERMANENT_VIP_RESOURCES
        other = ActivityCampaign.objects.create(key='explorer-vip', title='VIP', config={'theme': 'permanent-vip'})
        ActivityService.claim_for_space(other, self.space)
        for kind, reward_id, key in PERMANENT_VIP_RESOURCES[:-1]:
            UserResourceInventory.grant_activity_resource(self.user, kind, reward_id, key, other.key)
        self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))
        kind, reward_id, key = PERMANENT_VIP_RESOURCES[-1]
        UserResourceInventory.grant_activity_resource(self.user, kind, reward_id, key, other.key)
        self.assertTrue(ExplorerService.completed_other_activity(self.user, self.campaign))

    def test_bgm_requires_full_catalog_and_final_auto_owned_track(self):
        other = ActivityCampaign.objects.create(key='explorer-bgm', title='BGM', config={'mode': 'bgm_collection'})
        run = ActivityService.claim_for_space(other, self.space)
        progress = UserActivityProgress.objects.create(user=self.user, campaign=other, space_activity=run)
        BgmParticipation.objects.create(progress=progress)
        with patch('Activity.bgm.BgmService.catalog', return_value=[{}] * 12), patch('Activity.bgm.BgmService.stats', return_value=(89, 10, 0)) as stats:
            self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))
            stats.return_value = (90, 9, 0)
            self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))
            stats.return_value = (90, 10, 0)
            self.assertTrue(ExplorerService.completed_other_activity(self.user, self.campaign))

    def test_other_space_rewardless_game_and_other_user_do_not_count(self):
        other = ActivityCampaign.objects.create(key='explorer-rewardless', title='Game', config={'mode': 'number_duel'})
        ActivityService.claim_for_space(other, self.space)
        self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))
        other.config = {'reward': {'reward_id': 'test.other-space'}}
        other.save(update_fields=['config'])
        peer = User.create(self.space, 'Another detective')
        UserResourceInventory.grant_activity_resource(peer, 'frame', 'test.other-space', 'test-frame', other.key)
        self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))

    def test_native_binding_evidence_is_local_and_rejects_revocation(self):
        import hashlib
        import uuid
        from cryptography.fernet import Fernet
        from User.chronelle_account import store
        from User.models import ChronelleAccount, ChronelleCredential
        self.binding.stop()
        client = 'coapp_explorer_test_application'
        cipher = Fernet(Fernet.generate_key())
        subject = str(uuid.uuid4())
        value = {'protocol': 'oauth-v1', 'binding': {'subject': subject, 'client_id': client,
                 'binding_id': str(uuid.uuid4()), 'binding_version': 1,
                 'expires_at': ExplorerService.END.timestamp()}, 'access_until': 0,
                 'access_token': 'test-access', 'refresh_token': 'test-refresh'}
        with patch('User.chronelle_account.config', return_value=(client, 'test-only', cipher)), patch('User.chronelle_account.remote', side_effect=AssertionError('Activity polling must not call Chronelle')):
            row = ChronelleAccount.objects.create(user=self.user)
            store(row, value)
            self.assertTrue(ExplorerService.chronelle_bound(self.user))
            key = hashlib.sha256((client+'\0'+subject).encode()).hexdigest()
            shared = ChronelleCredential.objects.create(pk=key)
            store(shared, {**value, 'revoked': True})
            self.assertFalse(ExplorerService.chronelle_bound(self.user))
            store(shared, value)
            store(row, {**value, 'protocol': 'legacy'})
            self.assertFalse(ExplorerService.chronelle_bound(self.user))

    def test_pooled_activity_requires_actual_milestone_and_personal_prizes(self):
        from Activity.models import ActivityMilestone, SpaceActivityReward, UserActivityReward
        other = ActivityCampaign.objects.create(key='explorer-pool', title='Pool', config={'personal_reward_pool': [{'reward_id': 'pool.personal'}, {'reward_id': 'pool.alternative'}]})
        run = ActivityService.claim_for_space(other, self.space)
        milestone = ActivityMilestone.objects.create(campaign=other, threshold=10, resource_type='frame', reward_pool=[{'reward_id': 'pool.chosen'}, {'reward_id': 'pool.not-chosen'}])
        progress = UserActivityProgress.objects.create(user=self.user, campaign=other, space_activity=run)
        self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))
        SpaceActivityReward.objects.create(space_activity=run, milestone=milestone, resource_type='frame', reward_id='pool.chosen', resource_key='pool-frame')
        UserResourceInventory.grant_activity_resource(self.user, 'frame', 'pool.chosen', 'pool-frame', other.key)
        personal = UserActivityReward.objects.create(progress=progress, resource_type='bubble', reward_id='pool.personal', resource_key='pool-bubble')
        UserResourceInventory.grant_activity_resource(self.user, 'bubble', 'pool.personal', 'pool-bubble', other.key)
        self.assertFalse(ExplorerService.completed_other_activity(self.user, self.campaign))
        personal.claimed_at = ExplorerService.START
        personal.save(update_fields=['claimed_at'])
        self.assertTrue(ExplorerService.completed_other_activity(self.user, self.campaign))

    def test_invalid_campaign_cannot_reach_reward_claim(self):
        response = self.client.post('/activities/nonexistent-explorer/explorer/rewards/1/claim', HTTP_AUTHORIZATION='Bearer '+auth.get_login_token(self.user)['auth'])
        self.assertEqual(response.status_code, 400)
