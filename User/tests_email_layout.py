from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import translation

from User.models import NotificationDelivery, NotificationEvent, NotificationEventTypeChoice, User
from utils.email_layout import avatar_url, email_body, message_row


class EmailLayoutTests(SimpleTestCase):
    def test_chinese_square_notifications_do_not_fall_back_to_english(self):
        actor = User(id=2, name='小满')
        for kind in (NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE,
                     NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT,
                     NotificationEventTypeChoice.SQUARE_COMMENT_LIKE,
                     NotificationEventTypeChoice.SQUARE_COMMENT_REPLY,
                     NotificationEventTypeChoice.SQUARE_COMMENT_MENTION):
            event = NotificationEvent(user=User(id=1, language='zh-CN'), actor=actor, event_type=kind)
            title, body = event.render_delivery_message()
            self.assertNotIn('New ', title)
            self.assertIn('小满', body)
            self.assertNotIn('your', body)

    def test_email_escapes_user_markup_and_rejects_unsafe_avatar_urls(self):
        html = email_body('<title>', message_row('<script>', '<img src=x onerror=alert(1)>'))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;img', html)
        for uri in ('javascript:alert(1)', '//example.test/a', 'http://example.test/a', 'https://u:p@example.test/a'):
            self.assertEqual(avatar_url(SimpleNamespace(tiny_json=lambda: {'avatar_uri': uri})), '')

    @patch.object(NotificationDelivery, '_message_digest_groups')
    def test_digest_has_avatar_and_chat_bubble_without_markdown(self, groups):
        actor = SimpleNamespace(name='小满', tiny_json=lambda: {'avatar_uri': 'https://example.test/avatar.jpg'})
        event = SimpleNamespace(actor=actor, payload={'chat_id': 1}, render_delivery_message=lambda **kwargs: ('Title', '你好 **原文** <script>'))
        deliveries = [SimpleNamespace(event=event)]
        groups.return_value = ([dict(name='周末闲聊', total_count=1, selected=deliveries)], 0)
        with translation.override('zh_CN'):
            html = NotificationDelivery._render_email_batch_body(deliveries, SimpleNamespace(hide_message_content=False))
        self.assertIn('https://example.test/avatar.jpg', html)
        self.assertIn('周末闲聊', html)
        self.assertIn('**原文**', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('## ', html)
        private = NotificationDelivery._render_email_batch_body(deliveries, SimpleNamespace(hide_message_content=True))
        self.assertNotIn('小满', private)
        self.assertNotIn('avatar.jpg', private)
        self.assertNotIn('原文', private)
