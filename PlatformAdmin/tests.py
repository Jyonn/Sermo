import datetime
import hashlib
import hmac
from base64 import b32decode

from django.test import RequestFactory, TestCase
from django.contrib.auth.models import AnonymousUser
from django.utils import timezone

from Config.models import CI, Config
from PlatformAdmin.models import PlatformAdminEmailCode, PlatformAdminSecurity, PlatformAuditLog
from PlatformAdmin.views import ChatMessageView, LoginView, MessageDeliveryView
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Space.models import Space
from User.models import User, UserRoleChoice
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

    def test_chat_audit_includes_deleted_messages_and_pagination(self):
        space = Space.objects.create(name='Audit Space', slug='audit-space', email='owner@example.com')
        user = User.objects.create(space=space, name='Audited User', role=UserRoleChoice.MEMBER)
        chat = Chat.objects.create(space=space, chat_type=ChatTypeChoice.GROUP, title='Audit Chat', created_by=user)
        ChatMember.objects.create(chat=chat, user=user, status=ChatMemberStatusChoice.ACTIVE)
        deleted = Message.objects.create(chat=chat, user=user, type=MessageTypeChoice.TEXT, content='deleted evidence', is_deleted=True)
        Message.objects.create(chat=chat, user=user, type=MessageTypeChoice.TEXT, content='visible message')
        request = RequestFactory().get(
            f'/platform-admin/chats/{chat.id}/messages?reason=incident&limit=1',
            HTTP_AUTHORIZATION=f'Bearer {auth.get_platform_admin_token("admin@example.com")["auth"]}',
        )

        first_page = ChatMessageView.as_view()(request, chat_id=chat.id)
        next_request = RequestFactory().get(
            f'/platform-admin/chats/{chat.id}/messages?reason=incident&limit=1&before={first_page["next_before"]}',
            HTTP_AUTHORIZATION=f'Bearer {auth.get_platform_admin_token("admin@example.com")["auth"]}',
        )
        second_page = ChatMessageView.as_view()(next_request, chat_id=chat.id)

        self.assertTrue(first_page['has_more'])
        self.assertEqual(second_page['messages'][0]['message_id'], deleted.id)
        self.assertTrue(second_page['messages'][0]['is_deleted'])

    def test_message_delivery_audit_is_read_only_and_logged(self):
        space = Space.objects.create(name='Delivery Space', slug='delivery-space', email='owner@example.com')
        user = User.objects.create(space=space, name='Sender', role=UserRoleChoice.MEMBER)
        chat = Chat.objects.create(space=space, chat_type=ChatTypeChoice.GROUP, title='Delivery Chat', created_by=user)
        message = Message.objects.create(chat=chat, user=user, type=MessageTypeChoice.TEXT, content='trace me')
        request = RequestFactory().get(
            f'/platform-admin/messages/{message.id}/deliveries?reason=delayed-push',
            HTTP_AUTHORIZATION=f'Bearer {auth.get_platform_admin_token("admin@example.com")["auth"]}',
        )

        payload = MessageDeliveryView.as_view()(request, message_id=message.id)

        self.assertEqual(payload['message']['message_id'], message.id)
        self.assertEqual(payload['recipients'], [])
        self.assertEqual(payload['totals']['deliveries'], 0)
        self.assertTrue(PlatformAuditLog.objects.filter(
            action='message.deliveries_viewed',
            target_id=message.id,
        ).exists())
