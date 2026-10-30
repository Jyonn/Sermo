from unittest.mock import patch
from django.test import TestCase

from User import tests_chronelle_account as fixtures
from User.models import ChronelleAccount


class RetiredChronelleTests(TestCase):
    setUp = fixtures.ChronelleAccountTests.setUp
    call = fixtures.ChronelleAccountTests.call

    def test_retired_routes_cannot_start_claim_or_unbind_any_integration(self):
        with patch('requests.request', side_effect=AssertionError('Retired routes must not contact Chronelle')):
            for path, method in [('/users/me/chronelle/authorize', 'post'),
                                 ('/users/me/chronelle/claim', 'post'),
                                 ('/users/me/chronelle', 'delete')]:
                with self.subTest(path=path):
                    response = getattr(self.client, method)(path, **self.headers)
                    self.assertEqual(response.status_code, 404)
        self.assertFalse(ChronelleAccount.objects.filter(user=self.user).exists())

    def test_archived_share_ids_are_not_binding_evidence_or_public_profile_data(self):
        from Activity.explorer import ExplorerService

        self.user.chronelle_calendar_id = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'
        self.user.chronelle_authorization = {'request_id': 'obsolete', 'expires_at': 9999999999}
        self.user.save(update_fields=['chronelle_calendar_id', 'chronelle_authorization'])
        self.assertFalse(ExplorerService.chronelle_bound(self.user))
        self.assertEqual(self.call().json()['body'], {'status': 'binding_required', 'rebind_required': False})
        for payload in (self.user.jsonl(), self.user.json_friend(), self.user.json_me()):
            self.assertNotIn('chronelle_calendar_id', payload)
            self.assertNotIn('chronelle_authorization', payload)
