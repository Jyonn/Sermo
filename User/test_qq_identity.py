import json
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from Friendship.models import Friendship
from Space.models import Space, SpaceFeatureGrant, SpaceFeatureKeyChoice, SpaceOperator
from Square.models import Statement, StatementComment
from User.models import QQIdentity, User, UserAccountKindChoice, UserContactVerificationCode
from User.qq_identity import claim_qq_identity, ensure_qzone_placeholder
from utils import auth


class QQIdentityTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='江中东西墙', slug='jzdxq', email='wall@example.com')
        SpaceFeatureGrant.set_granted(
            self.space,
            SpaceFeatureKeyChoice.QQ_IDENTITY_BINDING,
            True,
            granted_by='platform@example.com',
        )
        self.space.qq_binding_enabled = True
        self.space.save(update_fields=['qq_binding_enabled'])

    def test_placeholder_is_not_an_active_space_member(self):
        identity = ensure_qzone_placeholder(self.space, '1493732945', '江中东墙')
        placeholder = identity.user

        self.assertEqual(placeholder.account_kind, UserAccountKindChoice.IMPORTED_PLACEHOLDER)
        self.assertTrue(placeholder.is_deleted)
        self.assertFalse(placeholder.has_password)
        self.assertEqual(self.space.active_member_count(), 0)
        self.assertFalse(
            Friendship.objects.filter(user_low=placeholder).exists()
            or Friendship.objects.filter(user_high=placeholder).exists()
        )

    def test_claim_moves_imported_content_to_bound_user(self):
        identity = ensure_qzone_placeholder(self.space, '1493732945', '江中东墙')
        placeholder = identity.user
        statement = Statement.objects.create(space=self.space, user=placeholder, text='历史说说')
        comment = StatementComment.objects.create(statement=statement, user=placeholder, text='历史评论')
        member = User.create(space=self.space, name='东墙运营')

        claimed = claim_qq_identity(member, '1493732945')

        statement.refresh_from_db()
        comment.refresh_from_db()
        placeholder.refresh_from_db()
        self.assertEqual(claimed.user_id, member.id)
        self.assertIsNotNone(claimed.verified_at)
        self.assertEqual(statement.user_id, member.id)
        self.assertEqual(comment.user_id, member.id)
        self.assertEqual(placeholder.merged_into_id, member.id)

    def test_two_wall_accounts_use_the_same_binding_flow(self):
        east_identity = ensure_qzone_placeholder(self.space, '1493732945', '江中东墙')
        west_identity = ensure_qzone_placeholder(self.space, '2485931633', '江中西墙')
        east_operator = User.create(space=self.space, name='东墙运营')
        west_operator = User.create(space=self.space, name='西墙运营')
        SpaceOperator.objects.create(space=self.space, user=east_operator)
        SpaceOperator.objects.create(space=self.space, user=west_operator)

        claim_qq_identity(east_operator, east_identity.qq)
        claim_qq_identity(west_operator, west_identity.qq)

        self.assertEqual(QQIdentity.objects.get(qq='1493732945').user_id, east_operator.id)
        self.assertEqual(QQIdentity.objects.get(qq='2485931633').user_id, west_operator.id)

    def test_binding_before_import_avoids_creating_a_placeholder(self):
        operator = User.create(space=self.space, name='东墙运营')
        claim_qq_identity(operator, '1493732945')

        resolved = ensure_qzone_placeholder(self.space, '1493732945', '江中东墙')

        self.assertEqual(resolved.user_id, operator.id)
        self.assertFalse(
            User.objects.filter(
                space=self.space,
                account_kind=UserAccountKindChoice.IMPORTED_PLACEHOLDER,
            ).exists()
        )

    def test_claim_rejects_an_identity_bound_to_another_member(self):
        first = User.create(space=self.space, name='先绑定者')
        second = User.create(space=self.space, name='后绑定者')
        claim_qq_identity(first, '1493732945')

        with self.assertRaises(ValidationError):
            claim_qq_identity(second, '1493732945')

    def test_claim_requires_platform_grant_and_space_switch(self):
        disabled_space = Space.objects.create(name='普通空间', slug='normal-space', email='normal@example.com')
        member = User.create(space=disabled_space, name='普通成员')

        with self.assertRaises(Exception):
            claim_qq_identity(member, '1493732945')

        SpaceFeatureGrant.set_granted(
            disabled_space,
            SpaceFeatureKeyChoice.QQ_IDENTITY_BINDING,
            True,
            granted_by='platform@example.com',
        )
        with self.assertRaises(Exception):
            claim_qq_identity(member, '1493732945')

        disabled_space.qq_binding_enabled = True
        disabled_space.save(update_fields=['qq_binding_enabled'])
        self.assertEqual(claim_qq_identity(member, '1493732945').user_id, member.id)


class QQIdentityAPITests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='江中东西墙', slug='jzdxq-api', email='api@example.com')
        SpaceFeatureGrant.set_granted(
            self.space,
            SpaceFeatureKeyChoice.QQ_IDENTITY_BINDING,
            True,
            granted_by='platform@example.com',
        )
        self.space.qq_binding_enabled = True
        self.space.save(update_fields=['qq_binding_enabled'])
        self.user = User.create(space=self.space, name='普通成员', password='secret123')

    def authorization(self):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.user)['auth']}"}

    def post_json(self, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )

    @patch('User.views.send_verification_mail')
    def test_member_verifies_qq_mailbox_and_claims_imported_identity(self, send_mail):
        placeholder = ensure_qzone_placeholder(self.space, '1493732945', '江中东墙').user
        statement = Statement.objects.create(space=self.space, user=placeholder, text='历史说说')

        initial = self.client.get('/users/me/qq-identity', **self.authorization())
        code_response = self.post_json('/users/me/qq-identity/code', {'qq': '1493732945'})

        self.assertEqual(initial.status_code, 200, initial.content)
        self.assertTrue(initial.json()['body']['available'])
        self.assertFalse(initial.json()['body']['bound'])
        self.assertEqual(code_response.status_code, 200, code_response.content)
        self.assertEqual(code_response.json()['body']['target'], '1493732945@qq.com')
        verification = UserContactVerificationCode.objects.get(user=self.user)
        send_mail.assert_called_once()

        claim_response = self.post_json('/users/me/qq-identity', {
            'qq': '1493732945',
            'code': verification.code,
        })

        self.assertEqual(claim_response.status_code, 200, claim_response.content)
        self.assertTrue(claim_response.json()['body']['bound'])
        self.assertEqual(claim_response.json()['body']['qq'], '1493732945')
        statement.refresh_from_db()
        self.assertEqual(statement.user_id, self.user.id)

    @patch('User.views.send_verification_mail')
    def test_code_request_requires_space_switch(self, send_mail):
        self.space.qq_binding_enabled = False
        self.space.save(update_fields=['qq_binding_enabled'])

        response = self.post_json('/users/me/qq-identity/code', {'qq': '1493732945'})

        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(UserContactVerificationCode.objects.filter(user=self.user).exists())
        send_mail.assert_not_called()
