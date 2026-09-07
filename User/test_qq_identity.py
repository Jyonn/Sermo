from django.core.exceptions import ValidationError
from django.test import TestCase

from Friendship.models import Friendship
from Space.models import Space, SpaceOperator
from Square.models import Statement, StatementComment
from User.models import QQIdentity, User, UserAccountKindChoice
from User.qq_identity import claim_qq_identity, ensure_qzone_placeholder


class QQIdentityTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='江中东西墙', slug='jzdxq', email='wall@example.com')

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
