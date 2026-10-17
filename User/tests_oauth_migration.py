import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
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
from User.oauth import OAuthError, authorize, exchange, userinfo


class OAuthMigrationTests(TransactionTestCase):
    def legacy_runtime(self):
        source = Path(__file__).with_name('fixtures').joinpath('oauth_legacy_73b2edf.txt').read_text()
        namespace = {'__name__': 'frozen_legacy_oauth'}
        exec(compile(source, 'oauth_legacy_73b2edf', 'exec'), namespace)
        return namespace

    def test_additive_stages_keep_legacy_runtime_until_seed_is_ready(self):
        previous = [('User', '0082_user_chronelle_authorization')]
        latest = [('User', '0083_oauth_application_registry')]
        legacy = self.legacy_runtime()
        MigrationExecutor(connection).migrate(previous)
        try:
            space = Space.objects.create(name='Synthetic rollout', slug='rollout-test', email='synthetic@example.com')
            user = User.objects.create(space=space, name='Synthetic', email='verified@example.test', email_verified_at=timezone.now(), phone='+8613800138000', phone_verified_at=timezone.now())
            verifier = 'v' * 43
            challenge = legacy['base64'].urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            params = dict(client_id='supervision-local', redirect_uri='http://127.0.0.1:8001/api/auth/friendom/callback/', scope='profile', state='s' * 32,
                          code_challenge=challenge, code_challenge_method='S256', response_type='code')

            def old_roundtrip():
                code = parse_qs(urlsplit(legacy['authorize'](user, **params)).query)['code'][0]
                result = legacy['exchange'](client_id=params['client_id'], redirect_uri=params['redirect_uri'], code=code, code_verifier=verifier)
                self.assertEqual(legacy['userinfo'](result['access_token'])['nickname'], user.name)
                return result['access_token']

            # Stage A: old code keeps serving before and after additive DDL.
            token_before_ddl = old_roundtrip()
            MigrationExecutor(connection).migrate(latest)
            token_before_seed = old_roundtrip()
            snapshot = list(OAuthGrant.objects.values())
            # A premature traffic switch would fail: the test demonstrates why it is forbidden.
            with self.assertRaises(OAuthError):
                userinfo(token_before_ddl)
            seed('https://stillfolio.jyonn.space/auth/sermo/callback')
            self.assertEqual(list(OAuthGrant.objects.values()), snapshot)
            # Stage B: old and new code both handle original clients after seed.
            old_roundtrip()
            for token in (token_before_ddl, token_before_seed):
                self.assertEqual(userinfo(token)['nickname'], user.name)
            code = parse_qs(urlsplit(authorize(user, **params)).query)['code'][0]
            result = exchange(client_id=params['client_id'], redirect_uri=params['redirect_uri'], code=code, code_verifier=verifier)
            self.assertNotIn('email', userinfo(result['access_token']))
            self.assertEqual(legacy['userinfo'](result['access_token'])['nickname'], user.name)
            # Stage C: once disable is used, only the policy-compatible runtime is safe.
            from User.models import OAuthApplication
            app = OAuthApplication.objects.get(client_id=params['client_id'])
            mutate(actor='synthetic-admin@example.com', admin=True, app_id=app.id, data=dict(action='disable', version=app.version))
            with self.assertRaises(OAuthError):
                userinfo(result['access_token'])
            # This documents the unsafe old-code behavior, not a permitted rollback path.
            self.assertEqual(legacy['userinfo'](result['access_token'])['nickname'], user.name)
            with self.assertRaises(OAuthError):
                userinfo(result['access_token'])
            custom = mutate(actor='synthetic-admin@example.com', admin=True,
                            data=dict(action='create', client_id='rollout-custom', name='Synthetic custom', redirect_uris=['https://example.com/cb'], scopes=['profile']))
            custom_params = {**params, 'client_id': 'rollout-custom', 'redirect_uri': 'https://example.com/cb'}
            custom_code = parse_qs(urlsplit(authorize(user, **custom_params)).query)['code'][0]
            custom_token = exchange(client_id='rollout-custom', redirect_uri='https://example.com/cb', code=custom_code, code_verifier=verifier)['access_token']
            with self.assertRaises(legacy['OAuthError']):
                legacy['authorize'](user, **custom_params)
            mutate(actor='synthetic-admin@example.com', admin=True, app_id=custom['id'], data=dict(action='disable', version=custom['version']))
            with self.assertRaises(OAuthError):
                userinfo(custom_token)
            self.assertEqual(legacy['userinfo'](custom_token)['nickname'], user.name)
        finally:
            MigrationExecutor(connection).migrate(latest)

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
