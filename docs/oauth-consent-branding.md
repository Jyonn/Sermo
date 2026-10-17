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

Actual browser evidence so far: requester and Frienden icons both loaded, desktop width matched document width, and the missing-both state had zero consent buttons with distinct verification links. A later open Chrome extension UI blocked subsequent browser operations. Final mobile/dark layouts, image-error event rendering, dynamic status refresh and full contact-sheet return still require browser acceptance; component rendering and helper tests are not counted as those visual/interaction checks. No real verification message, real grant or production state was created.
