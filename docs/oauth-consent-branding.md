# OAuth consent branding and verification guide — local review

Not deployed. The live `.20` security policy remains unchanged; frontend pending release is `2026.10.17.21`.

## Cause and registry changes

The registry revision model, metadata endpoint and consent UI had no requester-icon field. This was a missing metadata chain, rather than a failed Abandon-specific image request. Added optional `icon_url` to each revision, management draft/edit/review output and approved public metadata. Only HTTPS without embedded credentials or fragments is accepted. The server does not download the image. Pending revision icons never replace the approved icon. The UI uses an image element with no referrer and falls back to a neutral application placeholder with an accessible label; no client-specific hardcoded logo is used.

The Abandon website currently declares the real existing logo `https://abandon.qijiong.work/assets/abandon.svg?v=e82ca44054de`; the synthetic local preview uses that asset. The browser confirmed both real icons loaded successfully. No production registry record has been changed. To display that brand after a future release, configure the optional icon URL on Abandon's actual existing client through its ordinary approved revision workflow, retaining its client ID, callbacks and scopes exactly. Do not run seeds, infer client IDs or introduce a global Abandon fallback.

Migration 0084 is additive and defaults all existing icon URLs to empty. It neither changes approved pointers nor grants or scopes. Apply this migration before routing traffic to the new backend; old backend code tolerates the extra column. No production migration or rollback has been run. If needed, roll application code back to `.20` backend `91f3a32` before optionally reversing 0084. Code querying the icon column must never run after that column is removed; retain dual-verification enforcement throughout rollback.

## Consent flow

The page has a compact two-icon header, a single app title, one account/space row and a permission summary. The app's optional full description is collapsed. Missing verification is shown in a separate card with email and phone completion states. Pending rows open the matching existing email/phone binding UI, without sending a code automatically. The footer's main action is verification until the server reports eligibility; no misleading disabled consent action is shown. Eligible users retain the normal explicit consent button.

Verification links retain the original opaque search string (including state, client, redirect, scope, PKCE and login hint) in router state and pending session storage. Only the internal OAuth authorization route can be resumed. The existing account menu supports the phone deep link as well as email; successful binding or closing the OAuth-originated sheet returns to the original request. Other contact flows retain their existing behavior. On return, focus/visibility refresh and the eligibility endpoint recheck server state. The server remains authoritative and no verification fields are inferred from profile data or changed by this UI.

## Local review and checks

`http://127.0.0.1:5176/tmp/oauth-consent-review.html` uses the actual authorization page and actual menu/contact forms with synthetic API implementations. All application network calls are blocked; contact-code and bind actions are local simulations only. The requester icon is a public existing brand asset. Controls cover both missing, email missing, phone missing, all verified, live eligibility update, icon failure, check failure and theme. The fixture is ignored and excluded from production output.

Backend: 38 isolated SQLite OAuth/eligibility/registry/avatar/migration tests, with 37 passes and the existing engine-specific concurrency test skipped. `manage.py check` passes. Frontend: 10 tests cover production verification-card rendering states, exact opaque return parameters, safe internal navigation, safe image URLs and existing callback/registry presentation security. Production build passes.

## Completed follow-up browser acceptance

The earlier Chrome interruption was intermittent extension UI occupancy, not a filesystem/permission approval rejection. Supported browser APIs subsequently worked without disabling protections, changing permissions or switching control mechanisms. No rejected action was bypassed.

- Real desktop rendering: both official icons load, document/viewport widths both 1512px. Missing-both state has distinct email/phone actions and no consent button.
- Real 390×844 viewport: document width 390px; light and dark layouts visually inspected. Dark card/text computed colors were `rgb(21,29,25)` / `rgb(238,245,241)`. Viewport override was reset afterward.
- Missing-email, missing-phone and all-complete controls show correct rows/main actions. A synthetic eligibility change followed by the real focus-refresh handler removes the guide and exposes consent only after eligibility becomes true.
- Requester image error invokes the neutral accessible fallback. Eligibility check failure shows retry plus a disabled checking action, without an enabled consent action.
- The actual `MenuPage` email form was exercised with `preview@example.test` and a local dummy code through stubbed APIs. Successful binding returned to the authorization component with the exact original route/search string, including state, redirect, client, scopes and PKCE; consent became available after fresh mocked eligibility. Phone deep link opens the actual phone form. Cancel/back and keyboard Enter/Escape preserve the original request too.
- The fixture uses `MemoryRouter`; production route helpers and actual menu/consent components are exercised, but a real production BrowserRouter session, real OTP provider and real grant exchange are intentionally not tested. A fixture-only resource inventory shape mismatch was corrected from object to array before the successful menu checks. These local results do not imply the production client icon has been configured.

Synthetic screenshots: `artifacts/oauth-consent-review/mobile-light.jpg`, `mobile-dark.jpg`, `desktop-light.jpg` in the shared workspace. No screenshot was uploaded to Library. No real verification message, real grant or production state was created.

## Exact existing client and minimal pending production metadata plan

Read-only production lookup on 2026-10-17 identified exactly one matching approved application:

| Field | Existing value to preserve |
| --- | --- |
| Application ID | `4` |
| Client ID | `supervision-web` (retain despite the displayed Abandon name) |
| Application version | `4` at inspection time |
| Approved revision | `6` at inspection time |
| Enabled / display name | `true` / `板凳 · Abandon` |
| Redirect URIs | `https://api.supervision.qijiong.work/api/auth/friendom/callback/` and `https://api.abandon.qijiong.work/api/auth/friendom/callback/` |
| Scopes / loopback exceptions | `email`, `profile` / none |

Only proposed delta: `icon_url = https://abandon.qijiong.work/assets/abandon.svg?v=e82ca44054de`. Retain description, owner, enabled state, both callbacks, scopes, client ID and every other field byte-for-byte. Do not create an `abandon` client or add phone scope. This plan has not been executed.

Provenance: the official `https://abandon.qijiong.work/` HTML declares that URL via `rel=icon` and `type=image/svg+xml`. Versioned and unversioned `/assets/abandon.svg` both returned 200, SVG content type, 408 bytes, cache max-age 600, and SHA-256 `e82ca44054def7f84b5161cf3f5ad6db54df875e6784075156d03094ed398e08`. The query version matches the first 12 hash characters. The underlying path is maintained by the Abandon site and is not an immutable archive; future brand updates should be rechecked rather than assuming permanent byte stability.

After explicit deployment/metadata approval: (1) apply additive 0084 and deploy compatible backend/frontend; (2) re-read application 4, abort/review if version 4, approved revision 6 or any preserved field has drifted, and stop if an unfinished draft/pending revision exists; (3) create an ordinary revision copied from the current approved configuration with only icon URL changed, then submit and approve via the existing version-checked workflow using returned IDs/versions (never guess a revision ID); (4) read public metadata for both exact callbacks and verify the icon and unchanged scope set, without creating a grant; (5) retain old revision 6 and audit entries. No seed command or direct SQL update is part of this plan. To revert branding, submit a normal revision restoring the previous icon metadata while retaining the `.20` dual-verification rule.
