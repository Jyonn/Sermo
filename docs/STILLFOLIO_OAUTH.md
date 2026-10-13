# Stillfolio sign-in contract

Sermo exposes a single registered public OAuth-style client: `stillfolio`. Stillfolio is not yet integrated. No user data is sent when the user merely opens Stillfolio from the Sermo menu.

## Browser flow

1. Stillfolio generates and stores a random `state` (16-512 characters) and a PKCE verifier (43-128 unreserved characters). Compute `code_challenge = base64url(sha256(verifier))` without padding.
2. Navigate to `https://sermo.jyonn.space/oauth/authorize?client_id=stillfolio&redirect_uri=https%3A%2F%2Fstillfolio.jyonn.space%2Fauth%2Fsermo%2Fcallback&response_type=code&scope=profile%20phone%20vip&state=...&code_challenge=...&code_challenge_method=S256`. Sermo shows the consent page. The server checks the exact registered redirect URI; it does not accept arbitrary callback URLs.
3. After consent Sermo redirects to the callback with `code` and unchanged `state`. Stillfolio verifies the state. The code expires after five minutes and can be exchanged only once.
4. Stillfolio's server sends `POST https://api.sermo.jyonn.space/users/oauth/token` with `grant_type=authorization_code`, `client_id=stillfolio`, `redirect_uri`, `code`, and `code_verifier`. JSON and form bodies are supported. The one-hour bearer token must remain server-side.
5. Stillfolio's server calls `GET https://api.sermo.jyonn.space/users/oauth/userinfo` with `Authorization: Bearer <access_token>`. `POST /users/oauth/revoke` with the same header revokes the token.

`profile` returns `sub`, `space_slug`, `nickname`; `phone` returns `phone` in `+86` format only for verified mainland numbers (otherwise `null`) and `phone_verified`; `vip` returns `is_vip` for permanent VIP status. `sub` includes the Sermo space and user ID, so identical nicknames or phone numbers never collide. `sub` and `space_slug` are always returned for account identity even if `profile` was not requested. The requested scopes are shown on Sermo's consent page.

Deployment can override the callback exactly with `SERMO_OAUTH_STILLFOLIO_REDIRECT_URI`; set the frontend build's `VITE_STILLFOLIO_CALLBACK_URI` to the same value. Stillfolio must serve the callback URL (including direct navigation on its static host) before enabling SSO. Sermo's menu currently opens the Stillfolio home page rather than initiating a code flow because Stillfolio has not added the requesting side yet.
