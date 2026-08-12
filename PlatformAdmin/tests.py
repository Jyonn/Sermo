import datetime
import hashlib
import hmac
from base64 import b32decode

from django.test import RequestFactory, TestCase
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from Config.models import CI, Config
from PlatformAdmin.models import PlatformAdminEmailCode, PlatformAdminSecurity, PlatformAuditLog
from PlatformAdmin.views import LoginView
from Message.models import Message, MessageTypeChoice
from utils import auth


class PlatformAdminSecurityTests(TestCase):
    def setUp(self):
        Config.objects.update_or_create(key=CI.ADMIN_EMAIL, defaults={'value': 'admin@example.com'})

    def test_totp_accepts_current_counter(self):
        secret = PlatformAdminSecurity.new_secret()
        at = timezone.now().replace(microsecond=0)
        timestamp = int(at.timestamp())
        padded = secret + '=' * ((8 - len(secret) % 8) % 8)
        digest = hmac.new(b32decode(padded), (timestamp // 30).to_bytes(8, 'big'), hashlib.sha1).digest()
        index = digest[-1] & 0x0F
        code = f'{(int.from_bytes(digest[index:index + 4], "big") & 0x7FFFFFFF) % 1_000_000:06d}'
        self.assertTrue(PlatformAdminSecurity.verify_totp(secret, code, at=at))
        self.assertFalse(PlatformAdminSecurity.verify_totp(secret, 'not-a-code', at=at))

    def test_login_consumes_email_code_and_writes_audit_log(self):
        PlatformAdminEmailCode.objects.create(
            email='admin@example.com',
            code='123456',
            expires_at=timezone.now() + datetime.timedelta(minutes=5),
        )
        request = RequestFactory().post(
            '/platform-admin/login',
            data='{"email":"admin@example.com","code":"123456"}',
            content_type='application/json',
        )
        response = LoginView.as_view()(request)
        self.assertIn('auth', response)
        self.assertEqual(auth.decrypt(response['auth'], expected_type='platform_admin_access')['email'], 'admin@example.com')
        self.assertIsNotNone(PlatformAdminEmailCode.objects.get().consumed_at)
        self.assertTrue(PlatformAuditLog.objects.filter(action='auth.login').exists())

    def test_audit_serialization_ignores_anonymous_django_user(self):
        request = RequestFactory().get('/platform-admin/chats/1/messages')
        request.user = AnonymousUser()
        message = Message(type=MessageTypeChoice.MAP_ACCESS, content='{}')
        message._state.fields_cache['user'] = type('AuditMessageUser', (), {
            'space_id': 1,
            'tiny_json': lambda self: {'user_id': 1, 'name': 'sender'},
        })()

        payload = message._payload_for_type(request=request)

        self.assertEqual(payload['kind'], 'map_access')
        self.assertNotIn('access', payload)
        self.assertNotIn('chat_access', payload)
