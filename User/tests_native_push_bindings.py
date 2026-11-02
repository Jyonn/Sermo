import datetime
import uuid

from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from User.models import NativePushBinding as Binding, User
from User.native_push_models import NativePushConflict


class NativePushBindingTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(slug='native-a', name='A', email='a@example.invalid')
        self.other_space = Space.objects.create(slug='native-b', name='B', email='b@example.invalid')
        self.a = User.create(self.space, 'Alice', verified=True)
        self.b = User.create(self.other_space, 'Bob', verified=True)
        self.installation = uuid.uuid4()
        self.token = 'ab' * 32

    def prefer(self, user=None, enabled=True, revision=0, token=None, environment='sandbox'):
        return Binding.set_preference(user or self.a, self.installation, environment,
                                      opted_in=enabled, expected_revision=revision,
                                      token=self.token if token is None and enabled else token)

    def refresh(self, revision, token=None, user=None):
        return Binding.refresh_registration(user or self.a, self.installation, 'sandbox',
                                            token=token or self.token, expected_revision=revision)

    def test_accounts_in_distinct_spaces_have_independent_consent(self):
        a = self.prefer()
        b = self.prefer(self.b, enabled=False)
        self.assertEqual(a.space_id, self.space.id)
        self.assertEqual(b.space_id, self.other_space.id)
        self.assertEqual(Binding.active_for_user(self.a).count(), 1)
        self.assertFalse(Binding.active_for_user(self.b).exists())
        self.prefer(self.b, revision=b.revision)
        self.prefer(enabled=False, revision=a.revision)
        self.assertFalse(Binding.active_for_user(self.a).exists())
        self.assertTrue(Binding.active_for_user(self.b).exists())

    def test_token_refresh_does_not_create_consent(self):
        self.assertIsNone(self.refresh(0))
        self.assertFalse(Binding.objects.exists())

    def test_explicit_opt_out_creates_a_revisioned_tombstone(self):
        row = self.prefer(enabled=False)
        self.assertEqual(row.revision, 1)
        self.assertEqual(row.token, '')
        with self.assertRaises(NativePushConflict):
            self.refresh(0)
        row = self.refresh(1)
        self.assertFalse(row.opted_in)
        self.assertEqual(row.revision, 2)
        self.assertFalse(Binding.active_for_user(self.a).exists())

    def test_rotation_keeps_each_accounts_opt_out(self):
        a = self.prefer()
        b = self.prefer(self.b, enabled=False)
        a = self.refresh(a.revision, 'cd' * 32)
        b = self.refresh(b.revision, 'cd' * 32, self.b)
        self.assertTrue(a.opted_in)
        self.assertFalse(b.opted_in)
        self.assertEqual(a.token, b.token)
        self.assertEqual(Binding.active_for_user(self.a).count(), 1)
        self.assertFalse(Binding.active_for_user(self.b).exists())

    def test_stale_enable_and_stale_refresh_cannot_undo_opt_out(self):
        original = self.prefer()
        off = self.prefer(enabled=False, revision=original.revision)
        with self.assertRaises(NativePushConflict):
            self.prefer(revision=original.revision)
        with self.assertRaises(NativePushConflict):
            self.refresh(original.revision, 'cd' * 32)
        off.refresh_from_db()
        self.assertFalse(off.opted_in)
        self.assertEqual(off.token, self.token)

    def test_provider_invalidation_is_bound_to_token_and_revision(self):
        old = self.prefer()
        new = self.refresh(old.revision, 'cd' * 32)
        self.assertEqual(Binding.invalidate_token(old.id, old.token_digest, old.revision), 0)
        self.assertTrue(Binding.active_for_user(self.a).exists())
        self.assertEqual(Binding.invalidate_token(new.id, new.token_digest, new.revision), 1)
        self.assertFalse(Binding.active_for_user(self.a).exists())
        new.refresh_from_db()
        self.assertEqual(new.revision, 3)

    def test_same_invalid_token_stays_invalid_until_rotation(self):
        row = self.prefer()
        Binding.invalidate_token(row.id, row.token_digest, row.revision)
        row.refresh_from_db()
        row = self.refresh(row.revision)
        self.assertIsNotNone(row.invalidated_at)
        self.assertFalse(Binding.active_for_user(self.a).exists())
        row = self.refresh(row.revision, 'cd' * 32)
        self.assertIsNone(row.invalidated_at)
        self.assertTrue(Binding.active_for_user(self.a).exists())

    def test_sandbox_and_production_bindings_do_not_overwrite_each_other(self):
        sandbox = self.prefer()
        production = self.prefer(environment='production', token='cd' * 32)
        self.assertNotEqual(sandbox.pk, production.pk)
        self.prefer(enabled=False, revision=sandbox.revision)
        self.assertEqual(list(Binding.active_for_user(self.a).values_list('environment', flat=True)), ['production'])

    def test_lease_expiry_requires_an_authenticated_refresh(self):
        row = self.prefer()
        Binding.objects.filter(pk=row.pk).update(last_seen_at=timezone.now() - datetime.timedelta(days=46))
        self.assertFalse(Binding.active_for_user(self.a).exists())
        self.refresh(row.revision)
        self.assertTrue(Binding.active_for_user(self.a).exists())

    def test_status_never_serializes_token_or_digest(self):
        status = self.prefer().public_status()
        self.assertEqual(set(status), {'installation_id', 'environment', 'opted_in', 'revision', 'registered'})
        self.assertNotIn(self.token, str(status))
        self.assertTrue(status['registered'])

    def test_variable_length_tokens_are_normalized_without_a_fixed_apns_length(self):
        for length in (2, 64, 128, 1024):
            with self.subTest(length=length):
                value, digest = Binding._token('AB' * (length // 2))
                self.assertEqual(value, 'ab' * (length // 2))
                self.assertEqual(len(digest), 64)

    def test_invalid_inputs_cannot_create_bindings(self):
        for token in ('', 'abc', 'zz', 'ab\n', ' ab', 'a' * 1026, 123, True):
            with self.subTest(token=repr(token)), self.assertRaises(ValueError):
                self.prefer(token=token)
        for revision in (-1, True, '0', 1.0, 2 ** 63 - 1):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.prefer(revision=revision)
        for installation in ('bad', uuid.UUID(int=0), str(uuid.uuid4()).replace('-', '')):
            with self.subTest(installation=installation), self.assertRaises(ValueError):
                Binding.set_preference(self.a, installation, 'sandbox', opted_in=False, expected_revision=0)
        for environment in ('development', 'Production', '', None):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                self.prefer(environment=environment)
        self.assertFalse(Binding.objects.exists())

    def test_enabling_requires_a_token_and_does_not_partially_save(self):
        with self.assertRaises(ValueError):
            Binding.set_preference(self.a, self.installation, 'sandbox', opted_in=True, expected_revision=0)
        with self.assertRaises(ValueError):
            Binding.set_preference(self.a, self.installation, 'sandbox', opted_in=1, expected_revision=0, token=self.token)
        self.assertFalse(Binding.objects.exists())

    def test_database_uniqueness_and_account_space_change_fail_closed(self):
        row = self.prefer()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Binding.objects.create(user=self.a, space=self.space, installation_id=self.installation, environment='sandbox')
        User.objects.filter(pk=self.a.pk).update(space=self.other_space)
        with self.assertRaises(NativePushConflict):
            self.refresh(row.revision)
        row.refresh_from_db()
        self.assertEqual(row.space_id, self.space.pk)
