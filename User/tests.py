from unittest.mock import patch

from django.test import SimpleTestCase

from User.models import NotificationEvent


class NotificationEventDeliveryTests(SimpleTestCase):
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
