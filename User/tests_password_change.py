from datetime import timedelta
import json
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from User.models import User, UserNotificationChoice, UserPasswordRecoveryChallenge as Challenge, RefreshToken
from User.password_change import change_password
from User.password_policy import validate_new_password
from User.validators import UserErrors
from utils import auth, function


class PasswordChangeTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Passwords', slug='passwords', email='owner@example.com')
        self.user = User.create(space=self.space, name='Member', password='old-pass', email='member@example.com', verified=True)
        self.space.refresh_from_db()
        self.official = self.space.ensure_official_user()

    def challenge(self, user=None, channel=UserNotificationChoice.EMAIL):
        return Challenge.issue(user or self.user, channel)

    def test_old_password_and_refresh_revocation(self):
        token = auth.get_login_token(self.user)
        with self.assertRaises(UserErrors.PASSWORD_ERROR.__class__):
            change_password(self.user, 'new-pass', old_password='wrong')
        updated = change_password(self.user, 'new-pass', old_password='old-pass')
        self.assertTrue(function.verify_password('new-pass', updated.salt, updated.password))
        self.assertFalse(RefreshToken.objects.filter(user=self.user, revoked_at__isnull=True).exists())
        self.assertTrue(token['refresh'])

    def test_official_policy(self):
        for password in ('1234567890', 'abcdefghij', 'Short1!', 'A1!' * 50, 'Password123'):
            with self.subTest(password=password), self.assertRaises(UserErrors.OFFICIAL_PASSWORD_WEAK.__class__):
                validate_new_password(self.official, password)
        validate_new_password(self.official, 'BrightMoon42!')

    def test_official_email_change_and_password_login(self):
        challenge = self.challenge(self.official)
        change_password(self.official, 'BrightMoon42!', method='email', challenge_id=challenge.id, code=challenge.code)
        self.assertEqual(User.login(self.space, self.official.name, 'BrightMoon42!').id, self.official.id)
        with self.assertRaises(UserErrors.PASSWORD_RECOVERY_UNAVAILABLE.__class__):
            Challenge.find_user(self.space, self.official.name)

    def test_owner_and_channel_binding(self):
        challenge = self.challenge()
        for user, method in ((self.official, 'email'), (self.user, 'sms')):
            with self.assertRaises(UserErrors.PASSWORD_RECOVERY_CODE_INVALID.__class__):
                change_password(user, 'BrightMoon42!', method=method, challenge_id=challenge.id, code=challenge.code)
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 0)
        self.assertIsNone(challenge.used_at)

    def test_attempts_persist_and_correct_code_cannot_bypass_limit(self):
        challenge = self.challenge()
        for _ in range(5):
            with self.assertRaises(UserErrors.PASSWORD_RECOVERY_CODE_INVALID.__class__):
                change_password(self.user, 'new-pass', method='email', challenge_id=challenge.id, code='wrong')
        challenge.refresh_from_db()
        self.assertEqual(challenge.attempts, 5)
        with self.assertRaises(UserErrors.PASSWORD_RECOVERY_ATTEMPTS_EXCEEDED.__class__):
            change_password(self.user, 'new-pass', method='email', challenge_id=challenge.id, code=challenge.code)

    def test_expiry_replay_and_changed_contact(self):
        challenge = self.challenge()
        Challenge.objects.filter(pk=challenge.pk).update(code_expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(UserErrors.PASSWORD_RECOVERY_CODE_EXPIRED.__class__):
            change_password(self.user, 'new-pass', method='email', challenge_id=challenge.id, code=challenge.code)
        challenge.refresh_from_db()
        self.assertIsNotNone(challenge.used_at)

    def test_code_single_use(self):
        challenge = self.challenge()
        change_password(self.user, 'new-pass', method='email', challenge_id=challenge.id, code=challenge.code)
        with self.assertRaises(UserErrors.PASSWORD_RECOVERY_CODE_INVALID.__class__):
            change_password(self.user, 'other-pass', method='email', challenge_id=challenge.id, code=challenge.code)

    def test_contact_change_rejects_old_code(self):
        challenge = self.challenge()
        User.objects.filter(pk=self.user.pk).update(email='changed@example.com')
        with self.assertRaises(UserErrors.PASSWORD_RECOVERY_TOKEN_INVALID.__class__):
            change_password(self.user, 'new-pass', method='email', challenge_id=challenge.id, code=challenge.code)

    def test_sms_and_unverified_contacts(self):
        self.user.phone = '13800138000'
        self.user.save(update_fields=['phone'])
        with self.assertRaises(UserErrors.PASSWORD_RECOVERY_CHANNEL_INVALID.__class__):
            self.challenge(channel=UserNotificationChoice.SMS)
        self.user.phone_verified_at = timezone.now()
        self.user.save(update_fields=['phone_verified_at'])
        challenge = self.challenge(channel=UserNotificationChoice.SMS)
        updated = change_password(self.user, 'new-pass', method='sms', challenge_id=challenge.id, code=challenge.code)
        self.assertTrue(function.verify_password('new-pass', updated.salt, updated.password))

    def test_weak_password_does_not_consume_code(self):
        challenge = self.challenge(self.official)
        with self.assertRaises(UserErrors.OFFICIAL_PASSWORD_WEAK.__class__):
            change_password(self.official, 'weak', method='email', challenge_id=challenge.id, code=challenge.code)
        challenge.refresh_from_db()
        self.assertIsNone(challenge.verified_at)

    def test_code_delivery_and_cooldown(self):
        from User.views import _send_password_code
        with patch('User.views.send_verification_mail') as send:
            result = _send_password_code(self.official, UserNotificationChoice.EMAIL)
            self.assertTrue(result['challenge_id'])
            send.assert_called_once()
            with self.assertRaises(UserErrors.PASSWORD_RECOVERY_TOO_FREQUENT.__class__):
                _send_password_code(self.official, UserNotificationChoice.EMAIL)

    def test_passwordless_setup(self):
        user = User.create(space=self.space, name='Passwordless')
        self.assertTrue(change_password(user, 'new-pass').has_password)

    def test_authenticated_endpoints_and_legacy_payload(self):
        credentials = auth.get_login_token(self.user)
        headers = dict(HTTP_AUTHORIZATION='Bearer ' + credentials['auth'])
        response = self.client.get('/users/me/password', **headers).json()
        self.assertIsNotNone(response['body'], response)
        self.assertEqual(response['body']['channels'][0]['type'], 'email')
        response = self.client.post('/users/me/password', json.dumps(dict(
            old_password='old-pass', new_password='new-pass',
        )), content_type='application/json', **headers).json()
        self.assertIsNotNone(response['body'], response)
        self.assertTrue(response['body']['auth']['refresh'])
        self.assertEqual(RefreshToken.objects.filter(user=self.user, revoked_at__isnull=True).count(), 1)

    def test_authenticated_code_endpoint_and_no_anonymous_access(self):
        credentials = auth.get_login_token(self.official)
        headers = dict(HTTP_AUTHORIZATION='Bearer ' + credentials['auth'])
        with patch('User.views.send_verification_mail'):
            response = self.client.post('/users/me/password/code', json.dumps(dict(channel=UserNotificationChoice.EMAIL)),
                                        content_type='application/json', **headers).json()
        self.assertIsNotNone(response['body'], response)
        self.assertTrue(response['body']['challenge_id'])
        self.assertIsNone(self.client.get('/users/me/password').json()['body'])
