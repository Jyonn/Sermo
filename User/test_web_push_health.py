import json
from unittest.mock import patch
from django.core import signing
from django.test import TestCase
from django.utils import timezone
from requests import Response
from pywebpush import WebPushException
from Space.models import Space
from User.models import User, WebPushSubscription, NotificationEvent, NotificationDelivery
from User.web_push_receipts import receipt_token, record_receipt, SALT, MAX_AGE


class WebPushHealthTests(TestCase):
    def setUp(self):
        self.space=Space.objects.create(name='Push Health',slug='push-health',email='push-health@example.com')
        self.user=User.create(self.space,'Recipient')
        self.other=User.create(self.space,'Other')
        self.subscription=self.bind(self.user)
        self.event=NotificationEvent.objects.create(user=self.user,actor=self.other,space=self.space,event_type=1,payload={'content':'hello'})
        self.delivery=NotificationDelivery.objects.create(event=self.event,channel=0,web_subscription=self.subscription,route_key=f'web:{self.subscription.pk}')

    def bind(self,user,endpoint='https://push.example.test/shared',**options):
        return WebPushSubscription.register(user,endpoint,'key','auth','https://frienden.jyonn.space',**options)

    def post_receipt(self,token,phase='received',**extra):
        return self.client.post('/users/web-push/receipt',json.dumps({'token':token,'phase':phase,**extra}),content_type='application/json')

    def test_terminal_failure_invalidates_all_accounts_for_the_endpoint_only(self):
        shared=self.bind(self.other);different=self.bind(self.other,'https://push.example.test/other')
        response=Response();response.status_code=410;response._content=b'Gone'
        with patch('utils.webpush.send_web_push',side_effect=WebPushException('expired',response=response)):
            self.delivery._attempt_send_web()
        for item in (self.subscription,shared):
            item.refresh_from_db();self.assertFalse(item.enabled);self.assertIsNotNone(item.invalidated_at);self.assertTrue(item.opted_in)
        different.refresh_from_db();self.assertTrue(different.enabled)
        stale=self.bind(self.user,restore_only=True,previous_endpoint=self.subscription.endpoint)
        self.assertFalse(stale.enabled)
        explicit=self.bind(self.user)
        self.assertFalse(explicit.enabled)
        fresh=self.bind(self.user,'https://push.example.test/new',previous_endpoint=self.subscription.endpoint)
        self.assertTrue(fresh.enabled);self.assertIsNone(fresh.invalidated_at)

    def test_transient_errors_do_not_invalidate_a_subscription(self):
        with patch('utils.webpush.send_web_push',side_effect=TimeoutError('timeout')):
            self.delivery._attempt_send_web()
        self.subscription.refresh_from_db();self.assertTrue(self.subscription.enabled);self.assertIsNone(self.subscription.invalidated_at)

    def test_new_account_cannot_reactivate_a_known_dead_browser_endpoint(self):
        WebPushSubscription.invalidate_endpoint(self.subscription.endpoint_digest)
        other=self.bind(self.other)
        self.assertFalse(other.enabled);self.assertIsNotNone(other.invalidated_at)

    def test_receipts_are_idempotent_and_out_of_order_events_cannot_downgrade_success(self):
        token=receipt_token(self.delivery)
        self.assertEqual(self.post_receipt(token,'submitted').status_code,200)
        self.delivery.refresh_from_db();received=self.delivery.client_received_at;submitted=self.delivery.notification_submitted_at
        self.assertIsNotNone(received);self.assertIsNotNone(submitted)
        self.post_receipt(token,'received');self.post_receipt(token,'failed',error='NotAllowedError');self.post_receipt(token,'submitted')
        self.delivery.refresh_from_db();self.assertEqual(self.delivery.client_received_at,received);self.assertEqual(self.delivery.notification_submitted_at,submitted);self.assertEqual(self.delivery.client_error,'')

    def test_failed_receipts_store_only_a_safe_error_code_and_later_success_clears_it(self):
        token=receipt_token(self.delivery)
        self.assertEqual(self.post_receipt(token,'failed',error='private endpoint details').status_code,200)
        self.delivery.refresh_from_db();self.assertEqual(self.delivery.client_error,'UnknownError');self.assertIsNone(self.delivery.notification_submitted_at)
        self.post_receipt(token,'submitted');self.delivery.refresh_from_db();self.assertEqual(self.delivery.client_error,'')

    def test_tampered_expired_and_wrong_binding_tokens_are_rejected(self):
        token=receipt_token(self.delivery)
        self.assertEqual(self.post_receipt(token+'bad').status_code,400)
        wrong=signing.dumps({'delivery':self.delivery.pk,'endpoint':'other'},salt=SALT)
        self.assertEqual(self.post_receipt(wrong).status_code,400)
        with patch('django.core.signing.time.time',return_value=timezone.now().timestamp()-MAX_AGE-10):
            expired=receipt_token(self.delivery)
        self.assertEqual(self.post_receipt(expired).status_code,400)
        self.assertEqual(self.post_receipt(token,'unrecognized').status_code,400)
        self.delivery.refresh_from_db();self.assertIsNone(self.delivery.client_received_at)

    def test_web_payload_has_a_signed_receipt_without_subscription_secrets(self):
        payload=self.delivery._web_payload();self.assertIn('receipt_token',payload)
        self.assertNotIn('p256dh',payload);self.assertNotIn('auth',payload);self.assertNotIn('endpoint',payload)
        record_receipt(payload['receipt_token'],'received');self.delivery.refresh_from_db();self.assertIsNotNone(self.delivery.client_received_at)
