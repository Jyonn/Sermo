"""Real row-lock regressions; SQLite results cannot certify these cases."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from User.models import NotificationEvent, User
from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_models import NativePushBinding as Binding, NativePushConflict
from User import tests_native_push_bindings as fixtures


@skipUnless(connection.vendor == 'mysql', 'MySQL connection/locking acceptance only')
@skipUnlessDBFeature('has_select_for_update')
class NativePushConcurrencyTests(TransactionTestCase):
    setUp = fixtures.NativePushBindingTests.setUp
    prefer = fixtures.NativePushBindingTests.prefer

    def race(self, operation):
        barrier = Barrier(2)

        def run():
            close_old_connections()
            try:
                # Establish independent database sessions before releasing either worker.
                with connection.cursor() as cursor:
                    cursor.execute('SELECT CONNECTION_ID()')
                    session = cursor.fetchone()[0]
                barrier.wait(timeout=10)
                return session, operation()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(len({session for session, _ in results}), 2)
        return [value for _, value in results]

    def test_two_connections_cannot_claim_the_same_notification(self):
        self.prefer()
        for _ in range(5):
            event = NotificationEvent.objects.create(user=self.a, space=self.space, event_type=1, payload={})
            row = Delivery.enqueue(event, environments=('sandbox',))[0]

            def claim():
                claimed = Delivery.claim(row.pk, environments=('sandbox',))
                return claimed.claim_id if claimed else None

            results = self.race(claim)
            claims = [claim for claim in results if claim is not None]
            self.assertEqual(len(claims), 1)
            row.refresh_from_db()
            self.assertEqual(row.state, 'processing')
            self.assertEqual(row.claim_id, claims[0])

    def test_same_revision_concurrent_opt_out_has_one_winner(self):
        binding = self.prefer()
        user_id, installation, revision = self.a.pk, self.installation, binding.revision

        def disable():
            user = User.objects.get(pk=user_id)
            try:
                result = Binding.set_preference(user, installation, 'sandbox',
                    opted_in=False, expected_revision=revision, token=None)
                return result.revision
            except NativePushConflict:
                return 'conflict'

        self.assertCountEqual(self.race(disable), [revision + 1, 'conflict'])
        binding.refresh_from_db()
        self.assertFalse(binding.opted_in)
        self.assertEqual(binding.revision, revision + 1)
        self.assertFalse(Binding.active_for_user(self.a).exists())
