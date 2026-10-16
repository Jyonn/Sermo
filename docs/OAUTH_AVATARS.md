# Frienden 友间 OAuth avatars

## Endpoints and scopes

- Authorize: `https://sermo.jyonn.space/oauth/authorize` (also supports space-prefixed authorization paths).
- Exchange: `POST https://api.sermo.jyonn.space/users/oauth/token`.
- Profile: `GET https://api.sermo.jyonn.space/users/oauth/userinfo`.
- Revoke: `POST https://api.sermo.jyonn.space/users/oauth/revoke`.

Use the existing authorization-code + S256 PKCE + state flow described in
[the OAuth contract](STILLFOLIO_OAUTH.md). Only `profile` is needed for nickname
and avatar. Do not request phone or VIP scopes just to obtain an avatar.

Example profile request from the third-party backend (placeholder, not a credential):

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer <OAUTH_ACCESS_TOKEN>' \
  'https://api.sermo.jyonn.space/users/oauth/userinfo'
```

Example profile-only response:

```json
{
  "sub": "sermo:42:123",
  "space_slug": "example-space",
  "nickname": "Example User",
  "avatar_url": "https://sermo.jyonn.space/assets/avatars/v2/01.png",
  "avatar_updated_at": null
}
```

## Field meanings

- `avatar_url`: the current plain avatar as an absolute HTTPS image URL, or
  `null` for absent/unsupported/unconfigured avatars. There are no avatar frames,
  VIP marks or profile decorations. Never interpret a null as an empty URL.
- `avatar_updated_at`: nullable ISO 8601 timestamp. Currently always `null`,
  because Frienden has no reliable avatar-update timestamp. Never substitute
  login time or token issue time. Compare URLs instead.
- Without `profile`, neither field nor nickname is included. Other existing
  identity/scope fields retain their previous behavior.

## Image loading, caching and lifetime

Preset images already exist as standalone PNGs at `/assets/avatars/v2/01.png`
through `36.png`; no sprite-coordinate knowledge is required. They use the
existing static-host hashed/versioned asset policy; changing preset changes
the URL, and changing the preset catalog must change its version directory.
Static hosting must continue serving `.png` as `image/png` over HTTPS, without
Cookie requirements, hotlink protection, or a restrictive same-origin CORP.

Custom images are served by a stable capability URL under
`https://api.sermo.jyonn.space/users/oauth/avatar/<SIGNED_AVATAR_IDENTITY>`.
The signature is scoped to one user's current avatar identity, not an OAuth
token or an arbitrary storage path. It has no short time expiry and needs no
extra request headers or Cookie. Signing-key rotation invalidates these URLs.
It remains valid after the OAuth bearer expires or is revoked; it is a shareable
image URL, not a live profile-query credential. Do not expose it unnecessarily.

Custom image responses are raster PNG (`Content-Type: image/png`),
`X-Content-Type-Options: nosniff`, `Cross-Origin-Resource-Policy: cross-origin`,
`Access-Control-Allow-Origin: *`, and `Cache-Control: public, max-age=300`.
The proxy refreshes a 60-second upstream storage signature per fetch, never
redirects clients to an expiring storage URL, follows no upstream redirects,
accepts only the configured avatar storage host and avatar-key namespace,
and bounds image size. Other uploaded media and storage credentials are not exposed.
Errors return 404 with `no-store`, not an HTML error or cached broken image.

On avatar change, a newly fetched userinfo response returns a different URL.
Old custom-image identities return 404 on the next origin fetch (already cached
responses may remain visible for up to five minutes). Deleting the user also
invalidates origin access. No arbitrary user-ID avatar/profile lookup is provided.

Third-party backends may cache the bytes locally, keyed by the full avatar URL.
Refresh userinfo at sign-in and when the user explicitly refreshes their profile;
replace cached metadata when the URL changes. No refresh-token flow is added:
userinfo tokens last one hour; reauthorize when expired. Userinfo itself remains
`Cache-Control: no-store`. If an image fails, show a local fallback and refresh
userinfo with a valid bearer, rather than retaining a broken URL indefinitely.

## Chronelle and StillFolio configuration

StillFolio's existing client ID, callback, scopes and code exchange need no
changes. Existing `profile` grants automatically receive the additive fields;
clients may ignore unknown fields. No broader consent is introduced.

The current Frienden registry contains only `stillfolio` with its exact
StillFolio callback. Chronelle is **not yet a registered OAuth client** and must
not impersonate StillFolio or reuse its callback. Before Chronelle can initiate
Frienden sign-in, register its own client ID and exact callback through the
existing registration policy, then use that ID with `scope=profile`, unique
state and PKCE. This change deliberately does not add clients or redirect URIs.
Frienden's separate Chronelle calendar authorization is the opposite direction
and does not grant Chronelle a Frienden OAuth identity token.

No database migration, storage-bucket publicity change or third-party repository
change is required. Deploy the backend addition using the existing release
process and ensure the API ingress routes `/users/oauth/avatar/` to Django,
without adding auth requirements or overwriting image/cache/CORP response headers.
