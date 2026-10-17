# OAuth email and phone verification — pending release

Local implementation only. Production deployment requires a separate confirmation. Frontend pending release: `2026.10.17.20`; live reviewed plugin release `.19` is unchanged.

## Policy and enforcement

`User/oauth_eligibility.py:eligibility` is the single OAuth-provider policy and reporting function. Both nonblank current contacts **and** their corresponding `email_verified_at` / `phone_verified_at` must exist. Generic account level, VIP, role, submitted profile fields and requested scopes do not grant an exception. Deleted users are ineligible. These requirements apply even to a `profile`-only application; they do not add contact scopes or disclose phone/email to such apps.

Existing verification capability: `ContactVerificationCodeRequestView` sends email/SMS codes; `ContactBindingConfirmView` verifies the code before `bind_contact` writes the timestamp. Existing password setup may be required by that flow. No SMS provider, credential, account, authorization, verification state or historical grant is created/modified in production by this change. No secrets or deployment settings were loaded for tests.

Enforcement points:

- Authorization re-reads and locks the current user before creating a grant.
- Authorization-code exchange re-reads and locks the current user; denial does not consume a code or mint an access token.
- `grant_for_token` rechecks current eligibility for every identity read, including grants issued before this change. `userinfo` cannot bypass the policy.
- Previously issued custom-avatar links check the same policy before fetching an image; future responses use `Cache-Control: no-store`. Already cached old responses may survive their previous 300-second freshness period; already copied identity information cannot be recalled. Public preset images remain static assets.
- OAuth refresh grants remain unsupported (`unsupported_grant_type`); no OAuth refresh token has been introduced. Normal Frienden `/users/refresh` remains unchanged and works for users lacking dual verification.
- Revocation remains available to a user who lost eligibility. Ordinary login, developer applications and platform-admin application management retain their existing requirements.

Authenticated GET `/users/oauth/eligibility` reports only `eligible`, `missing_verifications` and the fixed account-security path, with `no-store`. The consent page uses it, disables approval until eligible, shows missing verification, and links to the existing account-security drawer. The authorization POST remains authoritative if verification changes after the page loads. Denial is HTTP 403, `error=verification_required`, an English error description, and `verification_path=/app/menu?panel=account-security`. Frontend copy is localized.

## Compatibility and production confirmation

Existing grants are neither deleted nor batch-revoked. Once either contact loses verification, code exchange and identity access fail immediately at the origin. Reverification restores access for an otherwise unexpired/unrevoked grant; expired codes (five minutes) or tokens (one hour) still require a new flow. Third-party applications may maintain independent sessions after a past successful sign-in. This change cannot log those sessions out remotely; it blocks subsequent Frienden identity calls. Clients should handle 403 `verification_required` by directing the user to Frienden verification and retrying a fresh consent flow when needed.

Before production: confirm the activation time and user communication for users with existing third-party sessions, confirm backend-first then frontend rollout, and decide whether to purge old custom-avatar cache entries or allow their previous five-minute TTL to expire. No database migration, seed command, contact backfill, grant deletion or new service configuration is required. Do not deploy frontend `.20` before the eligibility endpoint exists: it fails closed. A frontend rollback may retain this backend policy; a rollback to pre-policy backend code would remove the new security rule and requires explicit approval. Prefer fixes that preserve enforcement.

## Isolated validation

Use `/tmp/frienden-oauth-tests/bin/python manage.py ... --settings=Sermo.isolated_settings`. This settings module uses a temporary SQLite test database and aliases direct `Sermo.settings` imports to the isolated module. Do not use production settings or copy production data. Verification values in tests belong only to synthetic fixture users.

Focused tests cover each missing timestamp/contact, stale user objects, authorization and exchange denial, old/raw grants, preservation and reverification, current-avatar denial, HTTP error/reporting behavior, unsupported refresh, ordinary login/refresh and developer-management independence. Existing OAuth, registry, avatar and migration tests remain enabled. SQLite cannot validate MySQL `select_for_update` concurrency; the existing concurrency test is explicitly skipped rather than claimed as exercised.
