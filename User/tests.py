from unittest.mock import patch

from django.test import SimpleTestCase

from User.models import NotificationEvent, NotificationEventTypeChoice


class NotificationEventDeliveryTests(SimpleTestCase):
    def test_hidden_message_uses_custom_title_and_body(self):
        event = NotificationEvent(
            event_type=NotificationEventTypeChoice.DIRECT_MESSAGE,
            payload={'content': 'secret'},
        )

        title, body = event.render_delivery_message(
            hide_message_content=True,
            hidden_direct_message_title='自定义标题',
            hidden_direct_message_text='自定义内容',
        )

        self.assertEqual(title, '自定义标题')
        self.assertEqual(body, '自定义内容')

    def test_online_message_uses_custom_title_and_body(self):
        event = NotificationEvent(
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload={'kind': 'peer_online'},
        )

        title, body = event.render_delivery_message(
            friend_online_message_title='好友来了',
            friend_online_message_text='快去聊天',
        )

        self.assertEqual(title, '好友来了')
        self.assertEqual(body, '快去聊天')

    @patch('User.models.threading.Thread')
    @patch('User.models.transaction.on_commit')
    def test_delivery_thread_starts_only_after_commit(self, on_commit, thread):
        NotificationEvent._enqueue_deliveries_after_commit([12, 34])

        thread.assert_not_called()
        callback = on_commit.call_args.args[0]
        callback()

        thread.assert_called_once_with(
            target=NotificationEvent._enqueue_deliveries,
            args=((12, 34),),
            daemon=True,
            name='notification-delivery',
        )
        thread.return_value.start.assert_called_once_with()
