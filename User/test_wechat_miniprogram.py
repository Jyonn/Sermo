from unittest.mock import patch

from django.test import TestCase

from Space.models import Space
from User.models import User, WeChatMiniProgramIdentity
from utils import auth
from User.wechat_miniprogram import login_with_wechat_code


class WeChatMiniProgramLoginTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='JZDXQ', slug='jzdxq', email='admin@example.com')

    @patch('User.wechat_miniprogram.exchange_code')
    def test_first_login_creates_bound_passwordless_user(self, exchange):
        exchange.return_value = dict(app_id='wx-test', open_id='openid-1', union_id='union-1')
        user, created = login_with_wechat_code('code', nickname='小姜')
        self.assertTrue(created)
        self.assertEqual(user.name, '小姜')
        self.assertFalse(user.has_password)
        self.assertTrue(WeChatMiniProgramIdentity.objects.filter(user=user).exists())
        self.assertTrue(user.has_capability('menu.profile.avatar.custom'))
        self.assertTrue(user.has_capability('menu.profile.nickname'))

    @patch('User.wechat_miniprogram.exchange_code')
    def test_follow_up_login_reuses_user(self, exchange):
        exchange.return_value = dict(app_id='wx-test', open_id='openid-1', union_id='')
        first, _ = login_with_wechat_code('first', nickname='小姜')
        second, created = login_with_wechat_code('second', nickname='不应覆盖')
        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(WeChatMiniProgramIdentity.objects.filter(user=first).count(), 1)

    @patch('User.wechat_miniprogram.exchange_code')
    def test_duplicate_nickname_gets_stable_suffix(self, exchange):
        User.create(space=self.space, name='小姜', language='zh-CN')
        exchange.return_value = dict(app_id='wx-test', open_id='openid-2', union_id='')
        user, _ = login_with_wechat_code('code', nickname='小姜')
        self.assertEqual(user.name, '小姜2')

    def test_bound_user_can_change_nickname_without_password(self):
        user = User.create(space=self.space, name='旧名', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(user=user, app_id='wx-test', open_id='openid-3')
        token = auth.get_login_token(user)['auth']

        response = self.client.post(
            '/users/me/name',
            data={'name': '新名'},
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {token}',
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertEqual(user.name, '新名')
