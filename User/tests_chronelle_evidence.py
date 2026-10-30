import json
import uuid
from copy import deepcopy
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Activity.explorer import ExplorerService
from User import tests_chronelle_account as fixtures
from User.chronelle_account import BindingFailure, authority, config, credentials, store, unpack
from User.models import ChronelleCredential


class ChronelleEvidenceTests(TestCase):
    setUp = fixtures.ChronelleAccountTests.setUp
    bound = fixtures.ChronelleAccountTests.bound
    call = fixtures.ChronelleAccountTests.call

    def test_expired_access_token_is_still_bound_without_network_or_writes(self):
        row, value = self.bound(expired=True)
        ciphertext = row.sealed
        with patch('User.chronelle_account.remote', side_effect=AssertionError('No activity network')):
            self.assertTrue(ExplorerService.chronelle_bound(self.user))
        row.refresh_from_db()
        self.assertEqual(row.sealed, ciphertext)
        self.assertFalse(ChronelleCredential.objects.exists())

    def test_expired_grant_is_not_bound_even_with_a_fresh_access_token(self):
        row, value = self.bound()
        value['binding']['expires_at'] = timezone.now().timestamp()
        store(row, value)
        with patch('User.chronelle_account.remote') as remote:
            self.assertFalse(ExplorerService.chronelle_bound(self.user))
            self.assertEqual(self.call().json()['body']['status'], 'binding_required')
            with self.assertRaises(BindingFailure):
                credentials(self.user, 'https://chronelle.jyonn.space')
        remote.assert_not_called()

    def test_latest_shared_grant_repairs_stale_local_expiry_and_uncertainty(self):
        row, value = self.bound()
        shared = authority(value)
        newest = deepcopy(value)
        newest['binding']['binding_version'] = 2
        newest['access_token'] = 'newest-access'
        store(shared, newest)
        value['binding']['expires_at'] = 0
        value['renewal_uncertain'] = True
        store(row, value)
        self.assertTrue(ExplorerService.chronelle_bound(self.user))
        self.assertEqual(credentials(self.user, 'https://chronelle.jyonn.space')['access_token'], 'newest-access')

    def test_revoked_uncertain_legacy_and_foreign_shared_authorities_never_count(self):
        _, value = self.bound()
        shared = authority(value)
        wrong_subject = deepcopy(value)
        wrong_subject['binding']['subject'] = str(uuid.uuid4())
        wrong_client = deepcopy(value)
        wrong_client['binding']['client_id'] = 'coapp_another_application_12345'
        for invalid in [dict(value, revoked=True), dict(value, renewal_uncertain=True),
                        dict(value, protocol='legacy'), dict(value, binding={}), wrong_subject, wrong_client]:
            with self.subTest(invalid=invalid.get('protocol')):
                store(shared, invalid)
                self.assertFalse(ExplorerService.chronelle_bound(self.user))

    def test_incomplete_or_malformed_native_grants_do_not_count(self):
        row, value = self.bound()
        for field, invalid in [('subject', 'not-a-uuid'), ('binding_id', ''), ('binding_version', True),
                               ('expires_at', None), ('expires_at', float('nan'))]:
            bad = deepcopy(value)
            bad['binding'][field] = invalid
            store(row, bad)
            self.assertFalse(ExplorerService.chronelle_bound(self.user))
        row.sealed = config()[2].encrypt(json.dumps(['obsolete-state']).encode()).decode()
        row.save(update_fields=['sealed'])
        self.assertFalse(ExplorerService.chronelle_bound(self.user))
        self.assertEqual(self.call().json()['body']['status'], 'binding_required')

    @patch('User.chronelle_account.remote', return_value={'active': False})
    def test_observed_provider_revocation_updates_activity_evidence(self, remote):
        _, value = self.bound()
        self.assertTrue(ExplorerService.chronelle_bound(self.user))
        self.assertEqual(self.call().json()['body']['status'], 'binding_required')
        self.assertTrue(unpack(authority(value))['revoked'])
        self.assertFalse(ExplorerService.chronelle_bound(self.user))
        remote.assert_called_once()

    @patch('User.chronelle_account.remote', return_value={'active': False})
    def test_refresh_preflight_revocation_is_not_rolled_back(self, remote):
        _, value = self.bound(expired=True)
        with self.assertRaises(BindingFailure):
            credentials(self.user, 'https://chronelle.jyonn.space')
        self.assertTrue(unpack(authority(value))['revoked'])
        self.assertFalse(ExplorerService.chronelle_bound(self.user))

    def test_provider_revoked_or_expired_errors_require_rebinding_and_stop_counting(self):
        _, value = self.bound()
        shared = authority(value)
        for code in ('binding_revoked', 'binding_expired'):
            store(shared, value)
            with patch('User.chronelle_account.remote', side_effect=BindingFailure(code)):
                self.assertEqual(self.call().json()['body'], {'status': code, 'rebind_required': True})
            self.assertFalse(ExplorerService.chronelle_bound(self.user))

    @patch('User.chronelle_account.remote', side_effect=BindingFailure('chronelle_unavailable', 503))
    def test_provider_outage_does_not_erase_a_verified_binding(self, remote):
        _, value = self.bound()
        self.assertEqual(self.call().json()['body']['status'], 'chronelle_unavailable')
        self.assertTrue(ExplorerService.chronelle_bound(self.user))
        self.assertFalse(unpack(authority(value)).get('revoked'))

    @patch('User.chronelle_account.remote')
    def test_old_inactive_response_cannot_revoke_a_newer_concurrent_grant(self, remote):
        _, value = self.bound()
        shared = authority(value)
        newest = deepcopy(value)
        newest['binding']['binding_version'] = 2
        newest['binding']['binding_id'] = str(uuid.uuid4())

        def race(*args, **kwargs):
            store(shared, newest)
            return {'active': False}

        remote.side_effect = race
        self.assertEqual(self.call().json()['body']['status'], 'chronelle_unavailable')
        self.assertTrue(ExplorerService.chronelle_bound(self.user))
        self.assertEqual(unpack(shared)['binding']['binding_version'], 2)
        self.assertFalse(unpack(shared).get('revoked'))
