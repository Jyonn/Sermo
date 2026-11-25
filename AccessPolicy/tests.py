from django.test import TestCase

from AccessPolicy.catalog import get_capability
from AccessPolicy.engine import evaluate_capability, evaluate_expression, validate_expression
from AccessPolicy.models import PlatformCapabilityPolicy, SpaceCapabilityPolicy
from AccessPolicy.validators import AccessPolicyErrors
from AccessPolicy.views import _simulate
from Space.models import Space
from User.models import User, UserAccountLevelChoice, UserRoleChoice
from utils.auth import get_space_login_token


class ExpressionTests(TestCase):
    def test_nested_boolean_expression_supports_all_any_and_not(self):
        expression = validate_expression({
            'all': [
                {'field': 'growth_level', 'op': 'gte', 'value': 4},
                {'any': [
                    {'field': 'email_verified', 'op': 'eq', 'value': True},
                    {'not': {'field': 'permanent_vip', 'op': 'eq', 'value': False}},
                ]},
            ],
        })
        self.assertTrue(evaluate_expression(expression, {
            'growth_level': 4, 'email_verified': False, 'permanent_vip': True,
        }))
        self.assertFalse(evaluate_expression(expression, {
            'growth_level': 3, 'email_verified': True, 'permanent_vip': False,
        }))

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(type(AccessPolicyErrors.POLICY_INVALID)) as context:
            validate_expression({'field': 'is_superuser', 'op': 'eq', 'value': True})
        self.assertEqual(
            context.exception.identifier,
            AccessPolicyErrors.POLICY_INVALID.identifier,
        )


class CapabilityDecisionTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Policy Space', slug='policy-space', email='owner@example.com',
            admin_phone='+8613800000000', admin_phone_verified_at='2026-01-01T00:00:00Z',
            group_square_enabled=True,
        )
        self.user = User.objects.create(
            space=self.space, name='Member', lower_name='member',
            role=UserRoleChoice.MEMBER, account_level=UserAccountLevelChoice.VERIFIED,
            growth_score=5300, growth_level=18,
        )

    def test_default_level_gate_is_a_platform_policy(self):
        self.assertEqual(get_capability('chat.message.send.image').requirement, {})
        policy = PlatformCapabilityPolicy.objects.get(capability_key='chat.message.send.image')
        self.assertEqual(policy.requirement, {'field': 'growth_level', 'op': 'gte', 'value': 2})
        self.assertFalse(evaluate_capability(
            'chat.message.send.image', user=self.user, context={'growth_level': 1},
        ).allowed)
        self.assertTrue(evaluate_capability(
            'chat.message.send.image', user=self.user, context={'growth_level': 2},
        ).allowed)

    def test_video_requires_level_five_and_a_phone_verified_space(self):
        self.space.admin_phone_verified_at = None
        self.space.save(update_fields=['admin_phone_verified_at'])
        self.assertFalse(evaluate_capability(
            'chat.message.send.video', user=self.user, context={'growth_level': 18},
        ).allowed)

        self.space.admin_phone_verified_at = '2026-01-01T00:00:00Z'
        self.space.save(update_fields=['admin_phone_verified_at'])
        self.assertFalse(evaluate_capability(
            'chat.message.send.video', user=self.user, context={'growth_level': 4},
        ).allowed)
        self.assertTrue(evaluate_capability(
            'chat.message.send.video', user=self.user,
            context={
                'growth_level': 5,
                'verified': False,
                'email_verified': False,
                'phone_verified': False,
                'dual_verified': False,
            },
        ).allowed)

    def test_platform_preview_models_space_verification_separately(self):
        policy = PlatformCapabilityPolicy.objects.get(capability_key='chat.message.send.video')
        data = {
            'capability_key': 'chat.message.send.video',
            'policy': {
                'requirement': policy.requirement,
                'denial': policy.denial,
                'limits': policy.limits,
            },
        }
        email_space = _simulate(None, data, 'platform')
        phone_space = _simulate(
            None, {**data, 'space_verification': 'phone'}, 'platform',
        )

        self.assertFalse(next(
            row['allowed'] for row in email_space['rows']
            if row['growth_level'] == 18
            and row['verification'] == 'dual'
            and not row['permanent_vip']
        ))
        self.assertTrue(next(
            row['allowed'] for row in phone_space['rows']
            if row['growth_level'] == 5
            and row['verification'] == 'none'
            and not row['permanent_vip']
        ))

    def test_qr_friend_request_exception_is_explicit_in_platform_policy(self):
        self.user.account_level = UserAccountLevelChoice.BASIC
        self.user.email_verified_at = None
        self.user.phone_verified_at = None
        self.assertFalse(evaluate_capability('contacts.friend_request', user=self.user).allowed)
        self.assertTrue(evaluate_capability(
            'contacts.friend_request', user=self.user, context={'qr_invite': True},
        ).allowed)

    def test_space_policy_can_only_add_a_constraint(self):
        PlatformCapabilityPolicy.objects.update_or_create(
            capability_key='chat.message.send.image',
            defaults={'requirement': {'field': 'growth_level', 'op': 'gte', 'value': 10}},
        )
        SpaceCapabilityPolicy.objects.create(
            space=self.space,
            capability_key='chat.message.send.image',
            requirement={'field': 'growth_level', 'op': 'gte', 'value': 2},
        )
        decision = evaluate_capability(
            'chat.message.send.image', user=self.user, context={'growth_level': 8},
        )
        self.assertFalse(decision.allowed)
        self.assertIn('platform', {item['source'] for item in decision.failed})

    def test_parent_denial_applies_to_every_descendant(self):
        SpaceCapabilityPolicy.objects.create(
            space=self.space,
            capability_key='chat.message.send',
            denial={'field': 'permanent_vip', 'op': 'eq', 'value': False},
        )
        decision = evaluate_capability('chat.message.send.text', user=self.user)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.denied_by[0]['key'], 'chat.message.send')

    def test_limits_merge_using_the_stricter_numeric_value(self):
        PlatformCapabilityPolicy.objects.update_or_create(
            capability_key='square.statement.publish',
            defaults={'limits': {'daily': 5, 'weekly': 20}},
        )
        SpaceCapabilityPolicy.objects.create(
            space=self.space, capability_key='square.statement.publish',
            limits={'daily': 3, 'weekly': 30},
        )
        decision = evaluate_capability('square.statement.publish.text', user=self.user)
        self.assertEqual(decision.limits, {'daily': 3, 'weekly': 20})

    def test_space_switch_cannot_override_platform_closure_and_leaves_other_spaces_open(self):
        flash = 'chat.message.flash_image'
        SpaceCapabilityPolicy.objects.create(space=self.space, capability_key=flash, enabled=False)
        second_space = Space.objects.create(name='Other', slug='other-policy', email='other@example.test')
        self.assertFalse(evaluate_capability(flash, space=self.space).allowed)
        self.assertTrue(evaluate_capability(flash, space=second_space).allowed)
        self.assertTrue(evaluate_capability(
            'chat.message.send.image', space=self.space, context={'growth_level': 2},
        ).allowed)
        PlatformCapabilityPolicy.objects.create(capability_key='chat.message', enabled=False)
        reopened = SpaceCapabilityPolicy(space=self.space, capability_key=flash, enabled=True)
        decision = evaluate_capability(flash, space=Space.objects.get(pk=self.space.pk), space_overrides={flash: reopened})
        self.assertEqual(decision.denied_by, [{'key': 'chat.message', 'source': 'platform', 'reason': 'disabled'}])

    def test_group_modes_are_builtin_and_saved_without_overwriting_other_settings(self):
        PlatformCapabilityPolicy.objects.filter(capability_key__in=['chat.group.join', 'chat.group.send']).delete()
        authorization = 'Bearer ' + get_space_login_token(self.space)['auth']
        for mode in (0, 1, 2):
            response = self.client.post('/spaces/admin/permissions/group-participation',
                {'unverified_group_policy': mode}, content_type='application/json', HTTP_AUTHORIZATION=authorization)
            self.assertEqual(response.status_code, 200, response.content)
            self.space.refresh_from_db()
            self.assertEqual(self.space.unverified_group_policy, mode)
            self.assertTrue(self.space.chat_enabled)
            self.assertTrue(evaluate_capability('chat.group.send', space=self.space, context={'verified': True}).allowed)
            preview_join = _simulate(self.space, {'capability_key': 'chat.group.join'}, 'space')
            preview_send = _simulate(self.space, {'capability_key': 'chat.group.send'}, 'space')
            self.assertEqual(preview_join['rows'][0]['allowed'], mode >= 1)
            self.assertEqual(preview_send['rows'][0]['allowed'], mode >= 2)
        self.space.unverified_group_policy = 1
        self.space.save(update_fields=['unverified_group_policy'])
        response = self.client.post('/spaces/admin/settings', {
            'name': 'New name', 'group_square_enabled': 1, 'chat_enabled': 1, 'square_explore_enabled': 1,
            'member_limit': 5,
        }, content_type='application/json', HTTP_AUTHORIZATION=authorization)
        self.assertEqual(response.status_code, 200, response.content)
        self.space.refresh_from_db()
        self.assertEqual(self.space.unverified_group_policy, 1)

    def test_preview_reports_space_gate_publishing_limits_and_member_context(self):
        space = self.space
        space.square_free_post_enabled = False
        simulation = _simulate(space, {'capability_key': 'square.statement.publish'}, 'space')
        row = next(row for row in simulation['rows'] if row['verification'] == 'dual')
        self.assertFalse(row['allowed'])
        self.assertIn('square_free_post_enabled', {reason['reason'] for reason in row['denied_by']})
        self.assertEqual(row['limits'], {'daily': 1, 'weekly': 5})
        base = {'capability_key': 'chat.message.flash_image', 'space_verification': 'phone'}
        self.assertFalse(_simulate(None, base, 'platform')['rows'][0]['allowed'])
        self.assertTrue(_simulate(None, {**base, 'flash_forward': True}, 'platform')['rows'][0]['allowed'])
        disabled = _simulate(None, {**base, 'chat_enabled': False, 'flash_forward': True}, 'platform')
        self.assertEqual(disabled['rows'][0]['denied_by'][0]['reason'], 'chat_enabled')
