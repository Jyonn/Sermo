import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from Space.models import Space
from User.models import OAuthApplicationAudit, OAuthGrant, User
from User.management.commands.seed_oauth_applications import seed
from User.oauth_registry import RegistryError, mutate


class OAuthMigrationTests(TransactionTestCase):
    def test_registry_schema_forward_reverse_preserves_existing_grant(self):
        previous = [('User', '0082_user_chronelle_authorization')]
        latest = [('User', '0083_oauth_application_registry')]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        try:
            space = Space.objects.create(name='Synthetic migration', slug='migration-test', email='synthetic@example.com')
            # Avoid registration side effects/background notifications during schema tests.
            user = User.objects.create(space=space, name='Synthetic')
            OAuthGrant.objects.create(user=user, client_id='stillfolio', redirect_uri='https://stillfolio.jyonn.space/auth/sermo/callback',
                                      scopes=['profile'], code_challenge='v' * 43, code_hash=hashlib.sha256(b'synthetic-code').hexdigest(),
                                      code_expires_at=timezone.now(), access_token_hash=hashlib.sha256(b'synthetic-token').hexdigest(), access_expires_at=timezone.now())
            snapshot = list(OAuthGrant.objects.values())
            MigrationExecutor(connection).migrate(latest)
            seed('https://stillfolio.jyonn.space/auth/sermo/callback')
            seed('https://stillfolio.jyonn.space/auth/sermo/callback')
            self.assertEqual(list(OAuthGrant.objects.values()), snapshot)
            MigrationExecutor(connection).migrate(previous)
            self.assertEqual(list(OAuthGrant.objects.values()), snapshot)
            MigrationExecutor(connection).migrate(latest)
            seed('https://stillfolio.jyonn.space/auth/sermo/callback')
            self.assertEqual(list(OAuthGrant.objects.values()), snapshot)
        finally:
            MigrationExecutor(connection).migrate(latest)

    @skipUnlessDBFeature('has_select_for_update')
    def test_concurrent_admin_writes_have_one_winner_and_one_audit(self):
        app = mutate(actor='synthetic-admin@example.com', admin=True,
                     data=dict(action='create', client_id='concurrency-test', name='Synthetic', redirect_uris=['https://example.com/cb'], scopes=['profile']))
        barrier = Barrier(2)

        def change():
            try:
                barrier.wait(timeout=10)
                mutate(actor='synthetic-admin@example.com', admin=True, app_id=app['id'], data=dict(action='disable', version=app['version']))
                return 200
            except RegistryError as error:
                return error.status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: change(), range(2)))
        self.assertEqual(sorted(outcomes), [200, 409])
        self.assertEqual(OAuthApplicationAudit.objects.filter(application_id=app['id'], action='disable').count(), 1)
