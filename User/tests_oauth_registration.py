"""Pure registration checks without model imports, secrets, or database access."""
import ast
import os
from pathlib import Path
import re
import unittest


class OAuthRegistrationTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).with_name('oauth.py').read_text()
        names = {'SCOPES', 'CLIENT_SCOPES', 'PKCE_RE'}
        definitions = {'OAuthError', 'registered_redirect_uri', 'validate_authorization'}
        nodes = [node for node in ast.parse(source).body if
                 isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
                 or isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in definitions]
        self.namespace = {'os': os, 're': re}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), source, 'exec'), self.namespace)
        self.params = dict(client_id='supervision-local', redirect_uri='http://127.0.0.1:8001/api/auth/friendom/callback/',
                           scope='profile', state='s' * 32, code_challenge='c' * 43,
                           code_challenge_method='S256', response_type='code')

    def test_supervision_exact_profile_registration(self):
        self.assertEqual(self.namespace['validate_authorization'](**self.params), ['profile'])
        self.assertEqual(self.namespace['CLIENT_SCOPES']['supervision-local'], frozenset({'profile'}))

    def test_no_scope_or_callback_expansion(self):
        for changes in [{'scope': 'profile phone'}, {'scope': 'vip'}, {'code_challenge_method': 'plain'},
                        {'redirect_uri': 'http://localhost:8001/api/auth/friendom/callback/'},
                        {'redirect_uri': self.params['redirect_uri'] + '?x=1'},
                        {'redirect_uri': self.params['redirect_uri'].rstrip('/')},
                        {'redirect_uri': 'https://supervision.sigm.work/api/auth/friendom/callback/'},
                        {'redirect_uri': 'http://127.0.0.1:8002/api/auth/friendom/callback/'}]:
            with self.subTest(changes=changes), self.assertRaises(self.namespace['OAuthError']):
                self.namespace['validate_authorization'](**{**self.params, **changes})

    def test_existing_clients_unchanged(self):
        self.assertEqual(self.namespace['CLIENT_SCOPES']['chronelle'], frozenset({'profile'}))
        self.assertEqual(self.namespace['CLIENT_SCOPES']['stillfolio'], frozenset({'profile', 'phone', 'vip'}))
        self.assertEqual(self.namespace['registered_redirect_uri']('chronelle'), 'https://chronelle.jyonn.space/api/profile/sermo/callback/')
        self.assertIsNone(self.namespace['registered_redirect_uri']('unknown'))
