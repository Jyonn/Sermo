from django.test import SimpleTestCase, TestCase

from Space.models import Space
from User.growth import (
    EVENT_RULES,
    GROWTH_THRESHOLDS,
    LEVEL_REWARDS,
    PERSONALIZATION_LEVELS,
    resolve_event_rule,
)
from User.growth_notifications import begin_growth_awards, growth_award_total, reset_growth_awards
from User.models import GrowthEvent, User, UserFeatureDiscovery


class GrowthRuleTests(SimpleTestCase):
    def test_growth_curve_and_every_level_reward_are_complete(self):
        self.assertEqual(GROWTH_THRESHOLDS[-1], 5300)
        self.assertEqual(set(LEVEL_REWARDS), set(range(1, 19)))
        self.assertTrue(all(LEVEL_REWARDS[level] for level in range(1, 19)))

    def test_reward_rarity_follows_the_product_reward_audit(self):
        rewards = {reward['id']: reward for items in LEVEL_REWARDS.values() for reward in items}
        self.assertEqual(rewards['capability.image']['rarity'], 'rare')
        self.assertEqual(rewards['frame.butterfly']['rarity'], 'legendary')
        self.assertEqual(rewards['frame.comet']['rarity'], 'legendary')
        self.assertEqual(rewards['background.arcade']['rarity'], 'epic')
        self.assertEqual(rewards['background.jazz']['rarity'], 'rare')
        self.assertEqual(rewards['bubble.niko']['rarity'], 'legendary')
        self.assertEqual(rewards['bubble.baxian_lv']['rarity'], 'legendary')
        self.assertEqual(rewards['bubble.baxian_zhongli']['rarity'], 'legendary')
        self.assertEqual(rewards['bubble.baxian_he']['rarity'], 'legendary')
        self.assertEqual(rewards['frame.niko']['rarity'], 'legendary')
        self.assertEqual(rewards['background.noir']['rarity'], 'legendary')

    def test_every_reward_has_structured_display_metadata(self):
        for rewards in LEVEL_REWARDS.values():
            for reward in rewards:
                self.assertTrue(reward['title_key'])
                self.assertTrue(reward['description_key'])
                self.assertIn(reward['preview_kind'], {'live', 'image', 'before_after', 'collection'})
                self.assertIn(reward['implementation_status'], {'live', 'partial', 'planned'})
                self.assertTrue(reward['destination'])

    def test_personalization_catalog_has_one_reward_per_unlock(self):
        rewards = {(reward['category'], reward['asset_key']): reward['level'] for items in LEVEL_REWARDS.values() for reward in items}
        for style, level in PERSONALIZATION_LEVELS['chat_bubble_style'].items():
            self.assertEqual(rewards[('bubble', style)], level)
        for style, level in PERSONALIZATION_LEVELS['avatar_frame_style'].items():
            self.assertEqual(rewards[('frame', style)], level)

    def test_unknown_and_retired_events_are_invalid(self):
        self.assertIsNone(resolve_event_rule('daily:chat:2026-08-04'))
        self.assertIsNone(resolve_event_rule('social:plaza_friend'))
        self.assertIsNone(resolve_event_rule('made-up:event'))

    def test_valid_events_always_use_current_points(self):
        self.assertEqual(EVENT_RULES['security:email'].points, 30)
        self.assertEqual(EVENT_RULES['explore:image'].points, 20)
        self.assertEqual(EVENT_RULES['vip:permanent'].points, 500)
        self.assertEqual(resolve_event_rule('daily:verified_reply:2026-08-04:user-7').points, 3)
        self.assertEqual(resolve_event_rule('weekly:active_5_days:2026-W32').points, 25)

    def test_chat_and_square_exploration_events_are_registered(self):
        expected_points = {
            'explore:file': 15,
            'explore:link': 10,
            'explore:sticker_collect': 10,
            'explore:sticker_create': 20,
            'explore:sticker_send': 10,
            'explore:message_reply': 10,
            'explore:map_access': 20,
            'explore:share_statement': 15,
            'explore:pin_message': 10,
            'explore:square_statement': 20,
            'explore:square_image': 15,
            'explore:square_audio': 20,
            'explore:square_video': 25,
            'explore:square_friends': 10,
            'explore:square_comment': 15,
            'explore:square_reply': 15,
            'explore:square_like': 5,
            'explore:square_comment_like': 5,
        }
        self.assertEqual(
            {key: EVENT_RULES[key].points for key in expected_points},
            expected_points,
        )


class GrowthReconciliationTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Growth Space', slug='growth-space', email='admin@example.com')
        self.user = User.create(space=space, name='Member')

    def test_reconciliation_removes_unknown_events_and_repairs_points(self):
        valid = GrowthEvent.objects.create(
            user=self.user,
            event_key='explore:image',
            category='legacy',
            title='旧分值',
            points=300,
        )
        invalid = GrowthEvent.objects.create(
            user=self.user,
            event_key='daily:chat:2026-08-04',
            category='daily',
            title='旧聊天事件',
            points=20,
        )

        self.assertEqual(self.user.reconcile_growth(), 20)
        valid.refresh_from_db()
        self.assertEqual((valid.category, valid.title, valid.points), ('explore', '首次发送图片', 20))
        self.assertFalse(GrowthEvent.objects.filter(id=invalid.id).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.growth_score, 20)

    def test_daily_events_share_the_global_daily_cap(self):
        day = '2026-08-04'
        keys = [
            f'daily:first_verified_communication:{day}',
            f'daily:verified_conversation:{day}:user-2',
            f'daily:verified_conversation:{day}:user-3',
            f'daily:verified_group:{day}:chat-2',
            f'daily:verified_media:{day}:user-{self.user.id}:image',
            f'daily:different_contact:{day}:user-2',
            f'daily:different_contact:{day}:user-3',
            f'daily:verified_reply:{day}:user-2',
            f'daily:verified_reply:{day}:user-3',
        ]
        for key in keys:
            rule = resolve_event_rule(key)
            GrowthEvent.objects.create(
                user=self.user,
                event_key=key,
                category=rule.category,
                title=rule.title,
                points=rule.points,
            )

        self.assertEqual(self.user.reconcile_growth(), 40)
        self.assertEqual(GrowthEvent.period_points(self.user, 'daily', day), 40)

    def test_permanent_vip_reward_is_retained_during_reconciliation(self):
        self.user.award_growth('vip:permanent')
        self.user.award_growth('vip:permanent')

        self.assertEqual(self.user.reconcile_growth(), 500)
        self.assertEqual(
            GrowthEvent.objects.filter(user=self.user, event_key='vip:permanent').count(),
            1,
        )

    def test_only_effective_growth_awards_are_collected_for_the_response(self):
        token = begin_growth_awards()
        try:
            self.assertEqual(self.user.award_growth('explore:image'), 20)
            self.assertEqual(self.user.award_growth('explore:image'), 0)
            self.assertEqual(growth_award_total(), 20)
        finally:
            reset_growth_awards(token)


class FeatureDiscoveryTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Discovery Space', slug='discovery-space', email='admin@example.com')
        self.user = User.create(space=space, name='Member')
        self.user.growth_score = GROWTH_THRESHOLDS[2]
        self.user.growth_level = 3
        self.user.save(update_fields=['growth_score', 'growth_level'])

    def test_unlocked_features_remain_new_until_discovered(self):
        status = UserFeatureDiscovery.status_for(self.user)
        features = {feature['reward_id']: feature for feature in status['features']}

        self.assertTrue(features['capability.image']['is_new'])
        self.assertTrue(features['capability.audio']['is_new'])
        self.assertTrue(features['capability.location']['is_new'])

        updated = UserFeatureDiscovery.discover(self.user, 'capability.audio')
        updated_features = {feature['reward_id']: feature for feature in updated['features']}
        self.assertFalse(updated_features['capability.audio']['is_new'])
        self.assertTrue(updated_features['capability.location']['is_new'])
