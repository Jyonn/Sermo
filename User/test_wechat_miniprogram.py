from unittest.mock import patch

from django.test import TestCase

from Space.models import Space
from User.models import User, WeChatMiniProgramIdentity
from utils import auth
from User.wechat_miniprogram import begin_wechat_login, complete_wechat_onboarding, login_with_wechat_code


class WeChatMiniProgramLoginTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='JZDXQ', slug='jzdxq', email='admin@example.com')

    @patch('User.wechat_miniprogram.exchange_code')
    def test_first_login_waits_for_explicit_account_choice(self, exchange):
        exchange.return_value = dict(app_id='wx-test', open_id='openid-choice', union_id='')

        user, ticket, space = begin_wechat_login('code', space_slug=self.space.slug)

        self.assertIsNone(user)
        self.assertTrue(ticket)
        self.assertEqual(space, self.space)
        self.assertFalse(WeChatMiniProgramIdentity.objects.filter(open_id='openid-choice').exists())

    @patch('User.wechat_miniprogram.exchange_code')
    def test_existing_password_account_can_be_linked(self, exchange):
        existing = User.create(space=self.space, name='已有用户', password='secret123', language='zh-CN')
        exchange.return_value = dict(app_id='wx-test', open_id='openid-existing', union_id='')
        _, ticket, _ = begin_wechat_login('code', space_slug=self.space.slug)

        user, created = complete_wechat_onboarding(ticket, 'existing', nickname='已有用户', password='secret123')

        self.assertFalse(created)
        self.assertEqual(user, existing)
        self.assertTrue(WeChatMiniProgramIdentity.objects.filter(user=existing, open_id='openid-existing').exists())

    @patch('User.wechat_miniprogram.exchange_code')
    def test_existing_account_cannot_be_linked_to_second_wechat(self, exchange):
        existing = User.create(space=self.space, name='已有用户', password='secret123', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(
            user=existing, space=self.space, app_id='wx-test', open_id='openid-first',
        )
        exchange.return_value = dict(app_id='wx-test', open_id='openid-second', union_id='')
        _, ticket, _ = begin_wechat_login('code', space_slug=self.space.slug)

        with self.assertRaises(Exception) as raised:
            complete_wechat_onboarding(ticket, 'existing', nickname='已有用户', password='secret123')

        self.assertIn('already linked', str(raised.exception))
        self.assertFalse(WeChatMiniProgramIdentity.objects.filter(open_id='openid-second').exists())

    def test_bound_wechat_can_be_unlinked_with_password(self):
        user = User.create(space=self.space, name='微信用户', password='secret123', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(
            user=user, space=self.space, app_id='wx-test', open_id='openid-unbind',
        )
        token = auth.get_login_token(user)['auth']

        status = self.client.get('/users/me/wechat-miniprogram', HTTP_AUTHORIZATION=f'Bearer {token}')
        response = self.client.delete(
            '/users/me/wechat-miniprogram', data={'password': 'secret123'},
            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}',
        )

        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()['body']['bound'])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['body']['bound'])
        self.assertFalse(WeChatMiniProgramIdentity.objects.filter(user=user).exists())

    def test_wechat_unlink_rejects_wrong_password(self):
        user = User.create(space=self.space, name='微信用户', password='secret123', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(
            user=user, space=self.space, app_id='wx-test', open_id='openid-wrong-password',
        )
        token = auth.get_login_token(user)['auth']

        response = self.client.delete(
            '/users/me/wechat-miniprogram', data={'password': 'wrong-password'},
            content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {token}',
        )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(WeChatMiniProgramIdentity.objects.filter(user=user).exists())

    @patch('User.wechat_miniprogram.exchange_code')
    def test_passwordless_existing_account_cannot_be_linked(self, exchange):
        User.create(space=self.space, name='无密码用户', language='zh-CN')
        exchange.return_value = dict(app_id='wx-test', open_id='openid-no-password', union_id='')
        _, ticket, _ = begin_wechat_login('code', space_slug=self.space.slug)

        with self.assertRaises(Exception) as raised:
            complete_wechat_onboarding(ticket, 'existing', nickname='无密码用户', password='secret123')

        self.assertIn('Existing account must have a password', str(raised.exception))
        self.assertFalse(WeChatMiniProgramIdentity.objects.filter(open_id='openid-no-password').exists())

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
    def test_same_wechat_identity_can_join_multiple_spaces(self, exchange):
        other_space = Space.objects.create(name='另一空间', slug='another-space', email='other@example.com')
        exchange.return_value = dict(app_id='wx-test', open_id='openid-multi', union_id='union-multi')

        first, _ = login_with_wechat_code('first', nickname='小姜', space_slug=self.space.slug)
        second, created = login_with_wechat_code('second', nickname='小姜', space_slug=other_space.slug)

        self.assertTrue(created)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(second.space_id, other_space.id)
        self.assertEqual(WeChatMiniProgramIdentity.objects.filter(app_id='wx-test', open_id='openid-multi').count(), 2)

    @patch('User.wechat_miniprogram.exchange_code')
    def test_duplicate_nickname_gets_stable_suffix(self, exchange):
        User.create(space=self.space, name='小姜', language='zh-CN')
        exchange.return_value = dict(app_id='wx-test', open_id='openid-2', union_id='')
        user, _ = login_with_wechat_code('code', nickname='小姜')
        self.assertEqual(user.name, '小姜2')

    @patch('User.wechat_miniprogram.exchange_code')
    def test_long_nickname_is_trimmed_to_eight_characters(self, exchange):
        exchange.return_value = dict(app_id='wx-test', open_id='openid-4', union_id='')
        user, _ = login_with_wechat_code('code', nickname='一二三四五六七八九十')
        self.assertEqual(user.name, '一二三四五六七八')

    def test_bound_user_can_change_nickname_without_password(self):
        user = User.create(space=self.space, name='旧名', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(user=user, space=self.space, app_id='wx-test', open_id='openid-3')
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

    def test_passwordless_wechat_user_cannot_login_through_web(self):
        user = User.create(space=self.space, name='微信用户', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(
            user=user,
            space=self.space,
            app_id='wx-test',
            open_id='openid-web-login',
        )

        for password in (None, 'new-password'):
            response = self.client.post(
                '/spaces/join',
                data={
                    'slug': self.space.slug,
                    'name': user.name,
                    'password': password,
                    'language': 'zh-CN',
                },
                content_type='application/json',
            )

            self.assertEqual(response.status_code, 403)
            payload = response.json()
            self.assertEqual(payload['identifier'], 'USER@WECHAT_WEB_LOGIN_PASSWORD_REQUIRED')
            self.assertNotIn('auth', payload.get('body') or {})

        user.refresh_from_db()
        self.assertFalse(user.has_password)

    def test_wechat_user_can_login_through_web_after_setting_password(self):
        user = User.create(space=self.space, name='微信用户', language='zh-CN')
        WeChatMiniProgramIdentity.objects.create(
            user=user,
            space=self.space,
            app_id='wx-test',
            open_id='openid-password-set',
        )
        user.set_password('saved-password')

        response = self.client.post(
            '/spaces/join',
            data={
                'slug': self.space.slug,
                'name': user.name,
                'password': 'saved-password',
                'language': 'zh-CN',
            },
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('auth', response.json()['body'])
