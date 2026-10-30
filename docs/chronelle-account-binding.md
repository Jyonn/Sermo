# Chronelle account-binding OAuth

The current protocol is authorization-code OAuth with PKCE, confidential server
credentials and `authorization_type=account_binding`. Its sealed storage marker
is `oauth-v1`; that name does not refer to the retired display-only integration.
The current endpoint remains `/users/me/chronelle/account`.

The old `/users/me/chronelle/authorize`, `/users/me/chronelle/claim` and DELETE
`/users/me/chronelle` routes are removed. They cannot call `/api/integrations`,
create public-share calendar IDs or pretend to revoke a native OAuth grant.
The archived database columns and historical migrations remain for recovery;
runtime code no longer reads, writes or serializes those columns.

`User.chronelle_account.resolve_binding` owns cached binding readiness for the
account status, rotating credentials and Explorer evidence. It checks the native
protocol, configured client, UUID subject and grant ID, positive grant version,
finite grant expiry, credential presence, revocation and uncertain renewal.
An expired short-lived access token alone does not mean the account is unbound.
The newest shared authority wins over a stale space-local credential snapshot,
but must belong to the same client and subject.

Activity polling is read-only and makes no Chronelle requests or refreshes.
It knows local grant expiry and revocations already observed by account status
or refresh preflight; it does not promise immediate detection of an unobserved
provider-side revocation. Provider outages preserve verified local authority.
An inactive response is saved against the exact grant inspected, so an old
in-flight response cannot revoke a newer concurrent binding.

Completed Explorer themes and earned rewards remain earned. Newly evaluated
Chronelle checks use only the current native binding, never an old share ID.
