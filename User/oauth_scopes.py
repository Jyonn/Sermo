"""Delegated permissions, with unavailable actions kept out of the allowlist.

These are OAuth permissions, never ordinary session/JWT capabilities. A catalog
entry is descriptive until its adapter is implemented AND the rollout is on.
"""
from django.conf import settings


LEGACY_SCOPES = frozenset({'profile', 'email', 'phone', 'vip'})
DOMAINS = ('chat', 'square', 'submission', 'contacts', 'personal')
ACTIONS = ('read', 'write', 'modify')
UNSUPPORTED_SCOPES = frozenset({'square.modify', 'personal.write'})
IMPLEMENTED_SCOPES = frozenset({
    'chat.read', 'chat.write', 'chat.modify',
    'square.read', 'square.write',
    'submission.read', 'submission.write', 'submission.modify',
    'contacts.read', 'contacts.write', 'contacts.modify',
    'personal.read', 'personal.modify',
})


def delegation_enabled():
    # Deliberately fail closed for absent settings and strings such as "False".
    return getattr(settings, 'OAUTH_DELEGATED_ACTIONS_ENABLED', False) is True


def available_scopes():
    return LEGACY_SCOPES | (IMPLEMENTED_SCOPES if delegation_enabled() else frozenset())


def catalog():
    rows = [dict(scope=scope, domain='personal', action='read', status='legacy', available=True)
            for scope in ('profile', 'email', 'phone', 'vip')]
    for domain in DOMAINS:
        for action in ACTIONS:
            scope = f'{domain}.{action}'
            status = 'implemented' if scope in IMPLEMENTED_SCOPES else 'unsupported' if scope in UNSUPPORTED_SCOPES else 'planned'
            rows.append(dict(scope=scope, domain=domain, action=action, status=status,
                             available=scope in available_scopes()))
    return rows


def permission_groups(scopes):
    requested = set(scopes)
    return [dict(key=domain, scopes=[row['scope'] for row in catalog()
                                   if row['domain'] == domain and row['scope'] in requested])
            for domain in DOMAINS
            if any(row['domain'] == domain and row['scope'] in requested for row in catalog())]
