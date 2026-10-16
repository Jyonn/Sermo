# Stillfolio sign-in contract

Sermo exposes a single registered public OAuth-style client: `stillfolio`. Stillfolio has implemented the authorization-code flow. User profile, phone and VIP data are shared only after consent. The menu launch now targets Stillfolio's automatic sign-in entry; that new entry still needs implementation in Stillfolio. See [direct sign-in integration](STILLFOLIO_DIRECT_LOGIN.md).

## Browser flow

1. Stillfolio generates and stores a random `state` (16-512 characters) and a PKCE verifier (43-128 unreserved characters). Compute `code_challenge = base64url(sha256(verifier))` without padding.
2. Navigate to `https://sermo.jyonn.space/oauth/authorize?client_id=stillfolio&redirect_uri=https%3A%2F%2Fstillfolio.jyonn.space%2Fauth%2Fsermo%2Fcallback&response_type=code&scope=profile%20phone%20vip&state=...&code_challenge=...&code_challenge_method=S256`. Sermo shows the consent page. The server checks the exact registered redirect URI; it does not accept arbitrary callback URLs.
3. After consent Sermo redirects to the callback with `code` and unchanged `state`. Stillfolio verifies the state. The code expires after five minutes and can be exchanged only once.
4. Stillfolio's server sends `POST https://api.sermo.jyonn.space/users/oauth/token` with `grant_type=authorization_code`, `client_id=stillfolio`, `redirect_uri`, `code`, and `code_verifier`. JSON and form bodies are supported. The one-hour bearer token must remain server-side.
5. Stillfolio's server calls `GET https://api.sermo.jyonn.space/users/oauth/userinfo` with `Authorization: Bearer <access_token>`. `POST /users/oauth/revoke` with the same header revokes the token.

`profile` returns `nickname`, `avatar_url` and `avatar_updated_at`; `phone` returns `phone` in `+86` format only for verified mainland numbers (otherwise `null`) and `phone_verified`; `vip` returns `is_vip` for permanent VIP status. `sub` includes the Sermo space and user ID, so identical nicknames or phone numbers never collide. `sub` and `space_slug` are always returned for account identity even if `profile` was not requested. The requested scopes are shown on Sermo's consent page.

`avatar_url` is an absolute HTTPS image URL or `null` when no supported current
avatar is configured. It contains only the avatar, never frames or badges.
Preset avatars reuse individual PNG assets, not a sprite sheet. Custom avatars
use a narrowly scoped, signed Frienden image URL with no Cookie or Authorization
header required; its internal storage signatures are refreshed on each fetch.
`avatar_updated_at` is currently always `null`: the model does not record a
reliable avatar-change timestamp. Clients must accept unknown timestamps.
Both avatar fields are omitted entirely without `profile` consent.

See [OAuth avatar integration](OAUTH_AVATARS.md) for image caching, URL lifetime,
Chronelle onboarding requirements and credential-free examples. Existing
StillFolio scopes, exchange parameters, callback and token lifetimes are unchanged.

Deployment can override the callback exactly with `SERMO_OAUTH_STILLFOLIO_REDIRECT_URI`; set the frontend build's `VITE_STILLFOLIO_CALLBACK_URI` to the same value. Stillfolio must serve the callback URL, including direct navigation on its static host.

The authorization page also accepts an optional `login_hint` in `space_slug:user_id` format. This selects a locally saved Sermo account; it is not a credential or an identity assertion. The selected account must still have a valid session and approve the requested scopes. Hints are not included in the authorize POST, token exchange or userinfo response. The browser can move to `/{space_slug}/oauth/authorize` while retaining the original code challenge and state.
