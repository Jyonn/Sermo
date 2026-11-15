import datetime
import uuid

from django.test import TestCase
from django.utils import timezone

from User.models import NotificationEvent, NotificationTopicPreference as Pref
from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_models import NativePushBinding as Binding
from User import tests_native_push_bindings as fixtures


class NativePushDeliveryTests(TestCase):
    setUp = fixtures.NativePushBindingTests.setUp
    prefer = fixtures.NativePushBindingTests.prefer
    refresh = fixtures.NativePushBindingTests.refresh

    def event(self, user=None, payload=None):
        user = user or self.a
        return NotificationEvent.objects.create(user=user, space=user.space, event_type=1, payload=payload or {})

    def enqueue(self, event=None):
        return Delivery.enqueue(event or self.event(), environments=('sandbox',))

    def claim(self, row):
        return Delivery.claim(row.pk, environments=('sandbox',))

    def test_default_provider_gate_does_not_create_or_claim_rows(self):
        self.prefer()
        event = self.event()
        self.assertEqual(Delivery.enqueue(event), [])
        row = self.enqueue(event)[0]
        self.assertIsNone(Delivery.claim(row.pk))
        row.refresh_from_db()
        self.assertEqual(row.state, 'pending')

    def test_enqueue_is_idempotent_across_accounts_and_shared_tokens(self):
        self.prefer()
        self.prefer(self.b)
        first, second = self.event(), self.event(self.b)
        self.assertEqual(len(self.enqueue(first)), 1)
        self.assertEqual(self.enqueue(first), [])
        self.assertEqual(len(self.enqueue(second)), 1)
        self.assertEqual(Delivery.objects.count(), 2)
        self.assertNotEqual(first.native_deliveries.get().binding.user_id,
                            second.native_deliveries.get().binding.user_id)

    def test_all_topics_use_existing_system_notification_preferences(self):
        self.prefer()
        for payload, topic in (({}, 1), ({'kind': 'research_task'}, 8),
                               ({'kind': 'research_progress'}, 9), ({'kind': 'research_update'}, 10),
                               ({'notification_category': 'submission'}, 7), ({'kind': 'friend_statement'}, 11)):
            event = self.event(payload=payload)
            Pref.set_enabled(self.a, 0, topic, 0, False)
            self.assertEqual(self.enqueue(event), [])
            Pref.set_enabled(self.a, 0, topic, 0, True)
            row = self.enqueue(event)[0]
            Pref.set_enabled(self.a, 0, topic, 0, False)
            self.assertIsNone(self.claim(row))
            row.refresh_from_db()
            self.assertEqual(row.reason, 'policy_disabled')

    def test_queued_delivery_is_cancelled_after_opt_out_or_token_change(self):
        binding = self.prefer()
        row = self.enqueue()[0]
        self.prefer(enabled=False, revision=binding.revision)
        self.assertIsNone(self.claim(row))
        row.refresh_from_db()
        self.assertEqual((row.state, row.reason), ('skipped', 'binding_changed'))
        self.prefer(revision=binding.revision + 1)
        row = self.enqueue()[0]
        self.refresh(binding.revision + 2, 'cd' * 32)
        self.assertIsNone(self.claim(row))

    def test_same_token_refresh_also_invalidates_old_revision(self):
        binding = self.prefer()
        row = self.enqueue()[0]
        self.refresh(binding.revision)
        self.assertIsNone(self.claim(row))

    def test_one_accounts_opt_out_does_not_cancel_another_accounts_queue(self):
        binding = self.prefer()
        self.prefer(self.b)
        first, second = self.enqueue()[0], self.enqueue(self.event(self.b))[0]
        self.prefer(enabled=False, revision=binding.revision)
        self.assertIsNone(self.claim(first))
        self.assertIsNotNone(self.claim(second))

    def test_provider_invalidation_after_enqueue_prevents_claim(self):
        binding = self.prefer()
        row = self.enqueue()[0]
        Binding.invalidate_token(binding.pk, binding.token_digest, binding.revision)
        self.assertIsNone(self.claim(row))

    def test_new_claim_does_not_expire_and_missing_delivery_is_harmless(self):
        self.prefer()
        row = self.claim(self.enqueue()[0])
        self.assertEqual(Delivery.expire_claims(), 0)
        self.assertIsNone(Delivery.claim(row.pk + 1000, environments=('sandbox',)))
        row.refresh_from_db()
        self.assertEqual(row.state, 'processing')

    def test_invalidated_or_expired_bindings_are_excluded(self):
        binding = self.prefer()
        Binding.invalidate_token(binding.pk, binding.token_digest, binding.revision)
        self.assertEqual(self.enqueue(), [])
        Binding.objects.filter(pk=binding.pk).update(invalidated_at=None, last_seen_at=(
            timezone.now() - datetime.timedelta(days=46)))
        self.assertEqual(self.enqueue(), [])

    def test_environment_isolation_and_validation(self):
        self.prefer(environment='production')
        self.assertEqual(self.enqueue(), [])
        event = self.event()
        row = Delivery.enqueue(event, environments=('production',))[0]
        self.assertIsNone(self.claim(row))
        self.assertIsNotNone(Delivery.claim(row.pk, environments=('production',)))
        for environments in ('sandbox', ('invalid',), ([],), None):
            with self.assertRaises(ValueError):
                Delivery.enqueue(event, environments=environments)

    def test_claim_is_single_use_and_completion_requires_ownership(self):
        self.prefer()
        row = self.enqueue()[0]
        claimed = self.claim(row)
        self.assertIsNotNone(claimed)
        self.assertIsNone(self.claim(row))
        self.assertEqual(Delivery.finish(row.pk, uuid.uuid4(), 'sent'), 0)
        self.assertEqual(Delivery.finish(row.pk, claimed.claim_id, 'sent'), 1)
        self.assertEqual(Delivery.finish(row.pk, claimed.claim_id, 'failed'), 0)
        self.assertEqual(self.enqueue(row.event), [])

    def test_abandoned_claim_is_unknown_never_retried(self):
        self.prefer()
        row = self.claim(self.enqueue()[0])
        Delivery.objects.filter(pk=row.pk).update(claimed_at=timezone.now() - datetime.timedelta(minutes=16))
        self.assertEqual(Delivery.expire_claims(), 1)
        self.assertIsNone(self.claim(row))
        self.assertEqual(Delivery.finish(row.pk, row.claim_id, 'sent'), 0)
        row.refresh_from_db()
        self.assertEqual((row.state, row.reason), ('unknown', 'claim_expired'))

    def test_event_age_is_bounded_even_when_enqueued_late(self):
        self.prefer()
        event = self.event()
        NotificationEvent.objects.filter(pk=event.pk).update(created_at=timezone.now() - datetime.timedelta(hours=25))
        row = self.enqueue(event)[0]
        self.assertIsNone(self.claim(row))
        row.refresh_from_db()
        self.assertEqual(row.reason, 'expired')

    def test_deleted_or_moved_accounts_cannot_receive_queued_events(self):
        self.prefer()
        row = self.enqueue()[0]
        moved_row = self.enqueue()[0]
        type(self.a).objects.filter(pk=self.a.pk).update(is_deleted=True)
        self.assertIsNone(self.claim(row))
        self.a.refresh_from_db()
        self.assertEqual(self.enqueue(), [])
        type(self.a).objects.filter(pk=self.a.pk).update(is_deleted=False, space=self.other_space)
        self.assertIsNone(self.claim(moved_row))
        row.refresh_from_db()
        self.assertEqual(row.state, 'skipped')

    def test_provider_outcomes_are_allowlisted_and_do_not_invalidate_other_tokens(self):
        binding = self.prefer()
        row = self.claim(self.enqueue()[0])
        with self.assertRaises(ValueError):
            Delivery.finish(row.pk, row.claim_id, 'raw provider exception containing secrets')
        with self.assertRaises(ValueError):
            Delivery.finish(row.pk, str(row.claim_id), 'sent')
        self.assertEqual(Delivery.finish(row.pk, row.claim_id, 'rejected'), 1)
        binding.refresh_from_db()
        self.assertIsNone(binding.invalidated_at)
        row.refresh_from_db()
        self.assertEqual((row.state, row.reason), ('failed', 'provider_rejected'))
