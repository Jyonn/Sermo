# Drop · 一滴 OAuth picker

Frienden uses `diary.picker shares.create` with S256 PKCE and the callback
`https://api.sermo.jyonn.space/users/drop/callback`. Four existing Frienden/Sermo
origins are registered. The confidential client and Fernet storage key live only
in the service user's mode-0600 `~/.config/frienden/drop.json`.

`DropConnection` stores one encrypted grant per Frienden user, without the
phone-sharing or provider-specific phone-proof endpoints used by Pocklo and
StillFolio. OAuth state is consumed once; the same verifier and callback are used
for code exchange. Refresh rotation is serialized under a row lock; uncertain
rotation clears the local grant instead of replaying the previous refresh token.

API routes:

- `GET/POST /users/me/drop`: connection, connect, revoke/disconnect.
- `GET /users/me/drop/entries?q=&offset=`: authorized list/search, no-store.
- `POST /users/me/drop/shares`: validated `entry_ids`, `fields`, `expires_in`,
  and `operation_id`. An account-scoped SHA-256 idempotency key is forwarded.

No audio bytes are proxied. Sharing fields remain constrained by Drop's grant;
audio/location are not silently added. Disconnect revokes the OAuth grant and
does not revoke independent public links. The frontend validates exact iframe
origin/source and only accepts links created in the current picker session,
then asks for confirmation before sending to the current conversation.

Apply additive migration `User.0100_drop_connection` before switching the API
release. Existing deployed release trees must be preserved; do not overwrite the
dirty legacy `/home/ubuntu/Sermo` checkout. Keep OAuth keys outside releases.

Validation: isolated SQLite tests `User.tests_drop`, plus Pocklo and StillFolio
regression tests. These do not prove MySQL concurrency. Public Drop OAuth source
hashes were matched to the local contract. The registered client was accepted by
the live introspection endpoint; the embed page returns HTTP 200 with exact
`frame-ancestors` and the official icon returns image/png.
