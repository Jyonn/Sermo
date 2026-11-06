import datetime
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from User import tests_native_push_bindings as fixtures
from User.models import NotificationDelivery, NotificationEvent, NotificationEventTypeChoice, NotificationTopicPreference
from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_runtime import enqueue_native_event, process_native_queue, queue_environments
from utils.apns import APNsResult, APNsUnavailable


@override_settings(NATIVE_PUSH_QUEUE_ENABLED=True, NATIVE_PUSH_WORKER_ENABLED=True,
                   NATIVE_PUSH_ENVIRONMENTS=('sandbox',))
class NativePushRuntimeTests(TestCase):
    setUp = fixtures.NativePushBindingTests.setUp
    prefer = fixtures.NativePushBindingTests.prefer

    def event(self, user=None):
        user = user or self.a
        return NotificationEvent.objects.create(user=user, space=user.space,
            event_type=NotificationEventTypeChoice.SYSTEM, payload={'kind': 'research_task'})

    def queued(self, user=None, environment='sandbox'):
        return Delivery.enqueue(self.event(user), environments=(environment,))[0]

    def transport(self):
        value = Mock(configuration=SimpleNamespace(environments=('sandbox',)))
        value.send.return_value = APNsResult('accepted')
        return value

    def test_default_disabled_queue_has_no_binding_or_provider_work(self):
        with override_settings(NATIVE_PUSH_QUEUE_ENABLED=False), patch.object(Delivery, 'enqueue') as enqueue:
            self.assertEqual(enqueue_native_event(self.event()), [])
            self.assertEqual(process_native_queue(send=True)['status'], 'disabled')
            enqueue.assert_not_called()

    def test_configuration_is_strict_and_invalid_values_fail_closed(self):
        for enabled, environments in (('1', ('sandbox',)), (1, ('sandbox',)),
                                      (True, 'sandbox'), (True, ('sandbox', 'other')), (True, 1)):
            with override_settings(NATIVE_PUSH_QUEUE_ENABLED=enabled, NATIVE_PUSH_ENVIRONMENTS=environments):
                self.assertEqual(queue_environments(), ())
        with override_settings(NATIVE_PUSH_ENVIRONMENTS=('production', 'sandbox', 'sandbox')):
            self.assertEqual(queue_environments(), ('production', 'sandbox'))

    def test_event_pipeline_queues_only_opted_in_account_and_is_idempotent(self):
        self.prefer()
        self.prefer(self.b, enabled=False)
        a, b = self.event(), self.event(self.b)
        with patch('User.models.close_old_connections'), patch.object(NotificationDelivery, 'enqueue_for_event') as other:
            NotificationEvent._enqueue_deliveries([a.pk, b.pk])
            NotificationEvent._enqueue_deliveries([a.pk, b.pk])
        self.assertEqual(other.call_count, 4)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(Delivery.objects.get().event_id, a.pk)

    def test_native_queue_failure_does_not_block_other_routes_or_log_exception(self):
        self.prefer()
        event = self.event()
        with patch('User.models.close_old_connections'), patch.object(NotificationDelivery, 'enqueue_for_event') as other:
            with patch.object(Delivery, 'enqueue', side_effect=RuntimeError('SECRET_PROVIDER_TEXT')):
                with self.assertLogs('User.native_push_runtime', level='WARNING') as logs:
                    NotificationEvent._enqueue_deliveries([event.pk])
        other.assert_called_once()
        self.assertNotIn('SECRET_PROVIDER_TEXT', str(logs.output))
        self.assertNotIn(self.token, str(logs.output))

    def test_existing_topic_policy_prevents_native_enqueue(self):
        self.prefer()
        event = self.event()
        NotificationTopicPreference.set_enabled(self.a, 0, event.topic(), 0, False)
        self.assertEqual(enqueue_native_event(event), [])

    def test_event_hook_still_waits_until_commit_before_starting_thread(self):
        event = self.event()
        with patch('User.models.threading.Thread') as thread:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                NotificationEvent._enqueue_deliveries_after_commit([event.pk])
                thread.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
            self.assertEqual(thread.call_args.kwargs['target'], NotificationEvent._enqueue_deliveries)
            self.assertEqual(thread.call_args.kwargs['args'], ((event.pk,),))
            thread.return_value.start.assert_called_once()

    def test_dry_run_does_not_claim_expire_or_construct_provider(self):
        self.prefer()
        row = self.queued()
        with patch('User.native_push_runtime.make_transport') as factory, patch.object(Delivery, 'expire_claims') as expire:
            result = process_native_queue()
        factory.assert_not_called(); expire.assert_not_called()
        row.refresh_from_db()
        self.assertEqual((result['status'], result['candidates'], result['processed']), ('dry_run', 1, 0))
        self.assertEqual((row.state, row.claim_id), ('pending', None))

    def test_worker_requires_its_own_explicit_gate(self):
        self.prefer(); self.queued()
        with override_settings(NATIVE_PUSH_WORKER_ENABLED=False), patch('User.native_push_runtime.make_transport') as factory:
            self.assertEqual(process_native_queue(send=True)['status'], 'disabled')
        factory.assert_not_called()

    def test_send_batch_is_bounded_and_closes_transport(self):
        self.prefer()
        rows = [self.queued() for _ in range(3)]
        transport = self.transport()
        with patch('User.native_push_runtime.make_transport', return_value=transport):
            result = process_native_queue(limit=2, send=True)
        self.assertEqual((result['candidates'], result['processed'], result['sent']), (2, 2, 2))
        self.assertEqual(transport.send.call_count, 2)
        transport.close.assert_called_once()
        rows[2].refresh_from_db(); self.assertEqual(rows[2].state, 'pending')

    def test_opt_out_after_enqueue_does_not_cancel_another_account(self):
        binding = self.prefer(); self.prefer(self.b)
        a, b = self.queued(), self.queued(self.b)
        self.prefer(enabled=False, revision=binding.revision)
        transport = self.transport()
        with patch('User.native_push_runtime.make_transport', return_value=transport):
            result = process_native_queue(send=True)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.state, b.state, result['sent']), ('skipped', 'sent', 1))
        transport.send.assert_called_once()
        self.assertEqual(transport.send.call_args.kwargs['payload']['frienden']['account_id'], self.b.pk)

    def test_unknown_result_is_never_replayed_by_next_batch(self):
        self.prefer(); row = self.queued()
        transport = self.transport(); transport.send.return_value = APNsResult('unknown')
        with patch('User.native_push_runtime.make_transport', return_value=transport):
            self.assertEqual(process_native_queue(send=True)['unknown'], 1)
            self.assertEqual(process_native_queue(send=True)['processed'], 0)
        transport.send.assert_called_once()
        row.refresh_from_db(); self.assertEqual(row.state, 'unknown')

    def test_missing_provider_configuration_never_claims_or_exposes_details(self):
        self.prefer(); row = self.queued()
        with patch('User.native_push_runtime.make_transport', side_effect=APNsUnavailable('SECRET_KEY')):
            result = process_native_queue(send=True)
        self.assertEqual(result['status'], 'configuration_unavailable')
        self.assertNotIn('SECRET_KEY', str(result))
        row.refresh_from_db(); self.assertEqual((row.state, row.claim_id), ('pending', None))

    def test_global_gate_change_stops_remaining_batch(self):
        self.prefer(); rows = [self.queued(), self.queued()]
        transport = self.transport()
        def send(**kwargs):
            settings.NATIVE_PUSH_WORKER_ENABLED = False
            return APNsResult('accepted')
        transport.send.side_effect = send
        with override_settings(NATIVE_PUSH_WORKER_ENABLED=True), patch('User.native_push_runtime.make_transport', return_value=transport):
            result = process_native_queue(send=True)
        self.assertEqual((result['status'], result['processed'], result['sent']), ('stopped', 1, 1))
        rows[1].refresh_from_db(); self.assertEqual(rows[1].state, 'pending')

    def test_expired_claim_cleanup_is_environment_scoped(self):
        self.prefer(); self.prefer(environment='production')
        sandbox = self.queued(); production = self.queued(environment='production')
        Delivery.claim(sandbox.pk, environments=('sandbox',))
        Delivery.claim(production.pk, environments=('production',))
        Delivery.objects.update(claimed_at=timezone.now() - datetime.timedelta(minutes=16))
        transport = self.transport()
        with patch('User.native_push_runtime.make_transport', return_value=transport):
            result = process_native_queue(send=True)
        sandbox.refresh_from_db(); production.refresh_from_db()
        self.assertEqual((sandbox.state, production.state, result['expired_claims']), ('unknown', 'processing', 1))
        transport.send.assert_not_called()

    def test_management_command_defaults_to_dry_run_and_rejects_unbounded_limits(self):
        self.prefer(); self.queued()
        output = io.StringIO()
        with patch('User.native_push_runtime.make_transport') as factory:
            call_command('process_native_notifications', stdout=output)
        factory.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')
        for limit in (0, -1, 501):
            with self.assertRaises(CommandError):
                call_command('process_native_notifications', limit=limit, stdout=io.StringIO())
        for limit in (True, 0, 501):
            with self.assertRaises(ValueError):
                process_native_queue(limit=limit)
