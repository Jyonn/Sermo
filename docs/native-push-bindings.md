# Native Push Binding Foundation

## Current Backend Compatibility Rehearsal

Development branch `codex/native-push-current` integrates `origin/master` at
`2bde0dc` with the existing native push development head `e4ffebe`. It does not
update either original branch or deploy anything. Preserve the current birthday
and browser receipt routes, the canonical `/users/me` no-store policy, and both
sets of User fields. Migration `0096_merge_native_push` has no operations and joins
the independent birthday and native delivery migration leaves. Existing migration
names are not rewritten. Chinese catalogs are regenerated from the merged source.

The native branch's existing atomic permanent-VIP safeguards remain, while the
current participation metadata is preserved. Its old activity fixtures now declare
the actual Square/admin-verification prerequisites instead of weakening policy.
Research notification targets require both Chat and Research, matching the user's
supported configuration and native client; the earlier research-without-chat target
expectation is no longer valid. No independent research-chat endpoint is added.

The queue and worker still default off; public status still reports
`delivery_available=false`. This is not deployment, APNs configuration, signed
device delivery, MySQL locking acceptance or authorization to enable a worker.
Review the entire development diff, including its earlier VIP safeguards, before
seeking a separately scoped production deployment approval.

Initial focused checks exposed ten obsolete activity-fixture errors. After fixing
fixtures, all 45 focused cases passed. The first combined attempt could not load
three provider modules because local HTTP/2 dependencies were absent; it is not a
passing result. A dedicated temporary venv inheriting existing server libraries
now contains the repository-declared `httpx[http2]>=0.28,<0.29` dependency without
installing it globally. All transport requests in tests use mocks, not Apple.

The migration plan is conflict-free and the test database applies both branches.
System checks pass with the existing app-initialization database-access warning.
The dry-run model-drift check is not green: Chat index renames and OAuthGrant
options/ID changes also reproduce on untouched `2bde0dc`; no corresponding
unrelated migration is included. Both comparison logs are retained under
`/private/tmp/frienden-push-current-*-drift.log`.

Final validation: 165 combined tests passed, including compiled Chinese native
error translations, both research feature gates, schema coexistence and the
retained account/topic/transport cases. Evidence:
`/private/tmp/frienden-push-current-regression-complete.log`. Final system and
whitespace checks passed. The earlier 163/164-case runs precede the last target
and catalog assertions; they are not substituted for this final run. Production
database rehearsal and real device/provider acceptance are still unverified.

Development only. Deployment requires separate user confirmation. Models,
authenticated binding endpoints, a queue and an explicitly invoked APNs adapter
exist on the development branch. A default-off event hook and manual batch worker
are implemented locally. None is live; no real notification has been sent.

## Isolation And Consent

- One binding per authenticated account, installation UUIDv4 and APNs environment.
  Space ownership comes from the locked database account, never a client space ID.
  No registration operation edits another account's binding, even when installation
  and token are shared. Sandbox and production remain independent.
- Explicit opt-out creates a tombstone with a revision. Background registration
  cannot create consent, re-enable an opted-out account or recreate a missing row.
  Every mutation requires the expected revision; stale enable/token callbacks fail.
- Account and binding rows are locked before writes. Provider invalidation matches
  binding ID, token digest and revision, then increments revision. An old failure
  cannot invalidate a newer token; refreshing the same invalid token does not revive
  it. A genuinely changed token clears invalidation without changing consent.
- Candidate bindings require opt-in, matching space, a token, no invalidation and a
  45-day lease. Token refresh renews the lease; it does not change the user's choice.
  Public status excludes both the token and its digest. `registered` describes token
  presence/validity, not configured provider availability or successful delivery.

APNs tokens are variable length, not assumed to be 32 bytes. Hex normalization
preserves their bytes; 1024 hex characters is an application request/storage bound,
not an assertion about the provider's token size. See [Apple's registration
callback documentation](https://developer.apple.com/documentation/uikit/uiapplicationdelegate/application(_:didregisterforremotenotificationswithdevicetoken:)).

## Validation

### Development API

All routes require the current account's authentication. Responses use the existing
API envelope and `Cache-Control: no-store, private`; tokens and token digests are
never returned. `delivery_available` remains false pending deployment approval,
provider configuration and end-to-end device validation.

- `GET /users/me/native-push`: exactly `installation_id` and `environment` query
  parameters. Missing bindings return revision 0 without creating consent.
- `PUT /users/me/native-push`: JSON installation/environment, `expected_revision`,
  `opted_in`, and optional `token`. This is the explicit preference operation.
- `DELETE /users/me/native-push`: JSON installation/environment and revision;
  records an opt-out tombstone rather than deleting consent history.
- `POST /users/me/native-push/token`: JSON installation/environment, revision and
  token. Background refresh preserves consent and cannot create a missing binding.

Unknown or repeated fields, malformed JSON and invalid identities are rejected with
400. Stale revisions return 409 (`NATIVE_PUSH@CONFLICT`). Client-supplied account or
space IDs are rejected; ownership always comes from authentication. Errors are
localized in Chinese and English.

API milestone verification: 27 focused binding/API tests and 81 combined
account/binding/API/topic regression tests passed. System checks and diff whitespace
checks passed. Regression log: `/private/tmp/frienden-native-push-api-regression.log`.
This does not validate MySQL concurrency or real APNs delivery.

### Manual Worker (Development Only)

`python manage.py process_native_notifications --limit 50` is read-only by default.
Both queue configuration and the exact boolean `NATIVE_PUSH_WORKER_ENABLED=True`
are required even for the dry run. `--send` explicitly requests processing; it is
not authorized for production by this implementation. The provider environment
set must exactly match the configured queue environments. Batch limits are 1-500.
The command prints fixed aggregate counts, never tokens, payloads or raw errors.
Inspect its JSON `status`: process exit alone does not certify successful sends.
`sent` means provider acceptance, not receipt or presentation on a device.

Each item rechecks current consent and policy. Runtime gate changes stop the
remaining batch. Stale claims become nonretryable `unknown` only in configured
environments; a sandbox worker cannot expire production claims. Queue errors do
not block existing browser/email delivery. There is no periodic job or automatic
worker activation. App registration/tap handling and public availability remain
disabled. No schema changes were added in this phase.

Local validation: 15 focused runtime tests and 109 combined binding/API/queue/
dispatch/payload/target/topic/transport tests passed using isolated SQLite and
mock providers. No real account, provider request or notification was used. This
does not certify production database concurrency or physical-device delivery.

### Durable Queue

`NativePushDelivery` adds one unique row per event/binding, not per device token:
two accounts sharing an installation still have independent notification consent.
The queue reuses the existing WEB/system notification topic policy, including the
current restriction that online notices are supported only by the instant channel.
It does not add another topic matrix or change browser delivery.

Both enqueue and claim require explicitly supplied configured environments; their
default is empty. The after-commit event hook requires the exact boolean setting
`NATIVE_PUSH_QUEUE_ENABLED=True` and a nonempty `NATIVE_PUSH_ENVIRONMENTS` list
containing only `sandbox` and/or `production`. No scheduler or live credentials
are connected. Claim rechecks the current account, space, topic policy, binding
lease, opt-in, token digest and revision. Any registration revision change cancels
the old queued row conservatively, even if its token bytes did not change. Event
age is limited to 24 hours, including events enqueued late.

Row-locked claims are single use. Completion must match the claim UUID and accepts
only fixed outcome codes; raw provider exceptions are never persisted. A claim
abandoned for 15 minutes becomes `unknown`, not retryable: the provider might have
accepted it before the worker stopped. This prioritizes avoiding duplicate alerts
over automatic retries of ambiguous sends. A future dispatcher must revalidate
eligibility immediately before network submission; queue claiming alone cannot
eliminate an opt-out racing with an already in-flight network request.

Queue milestone: 15 new tests and 96 combined account/API/binding/queue/topic tests
passed (`/private/tmp/frienden-native-push-queue-regression.log`). System checks and
whitespace checks passed. SQLite test setup applies migration 0095. Migration dry
run reports only the previously reproduced OAuthGrant drift, not queue drift.
MySQL lock/concurrency behavior and real provider outcomes remain unverified.

### Provider Adapter And Submission

`utils.apns.APNsTransport` uses a reusable HTTP/2-only HTTPX client, fixed Apple
hosts, TLS verification, bounded timeouts, no ambient proxy settings and no
redirect following. There are no automatic network retries. ES256 authentication
tokens are cached for 50 minutes; private signing keys are omitted from repr.
The normal HTTPX request log is redacted because the URL contains a device token.
Low-level HPACK debug output is suppressed because compressed header blocks may
contain authentication material. Other HTTPX logs remain available with token
paths and bearer strings redacted.

`APNsConfiguration.from_environment` is available for a future worker, but is not
called automatically. `FRIENDEN_APNS_ENABLED` must be exactly `1`; default is off.
Additional server-only keys are `FRIENDEN_APNS_TEAM_ID`, `FRIENDEN_APNS_KEY_ID`,
`FRIENDEN_APNS_TOPIC`, `FRIENDEN_APNS_ENVIRONMENTS` (comma-separated sandbox and/or
production), and `FRIENDEN_APNS_PRIVATE_KEY` (PEM). No values were configured.
Use keys authorized for the selected environment/topic. Key or team changes must
replace the transport and close its old connection pool, not mutate a live client.
No secret belongs in the iOS app, repository or notification payload.

`dispatch_native_delivery` claims a single row and rechecks eligibility before
submission. Chat events additionally require active membership, current message
visibility, an undeleted chat/message and no group notification mute. It uses the
existing localized notification text; the version-1 tap identity contains only
canonical account ID, space slug and event ID, not arbitrary event fields or URLs.
Alert text is bounded against actual UTF-8 JSON size. The receiver must still
authenticate and resolve the event through access-checked APIs; this metadata does
not grant access. Notifications use expiration 0 to avoid provider-side storage.

Only HTTP/2 200 with a matching response identity (when supplied) is accepted.
Queue state `sent` means provider acceptance, not device receipt. Timeouts and
unrecognized outcomes become terminal `unknown`. Only typed 410 invalidation
timestamps from Unregistered/ExpiredToken responses may invalidate a binding;
they must not predate its last registration or lie implausibly in the future.
Invalidation matches the submitted account/binding, token digest and revision.
Old in-flight rejection cannot invalidate a rotated token or another account.
Provider errors are persisted only as fixed outcomes, never raw response text.

Adapter milestone: 31 new transport/payload/dispatch tests; 127 combined regression
tests passed, including browser Web Push (`/private/tmp/frienden-apns-full-regression-final.log`).
System checks passed. Tests used generated temporary P-256 keys and HTTPX mock
transport, not real credentials or network delivery. They do not prove TLS/HTTP2
negotiation with Apple, production configuration, MySQL concurrency or receipt on
a signed physical device. HTTPX HTTP/2 dependencies were installed only in the
temporary development virtual environment.

Protocol references: [Apple requests](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns),
[Apple token authentication](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns),
[Apple responses](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns),
and [HTTPX HTTP/2](https://www.python-httpx.org/http2/).

Secret-free SQLite settings and a dedicated temporary virtual environment were
used. No production database or delivery credential was accessed.

- Focused native binding/topic tests: 20 passed.
- Final account/native binding/topic regression: 68 passed, including all 14 new
  native binding tests (`/private/tmp/frienden-native-push-binding-final-regression.log`).
- `manage.py check --settings=Sermo.isolated_test_settings`: no issues. Existing
  application-initialization database-access warning remains.
- Test database creation applies the new migration successfully. `makemigrations
  --check --dry-run` still reports pre-existing OAuthGrant manager/ID drift; the
  exact same two operations were reproduced on untouched base `8ff7da5`. They are
  intentionally not included in this migration. No native-binding drift remains.
- A broader 89-test attempt had 16 errors in existing notification-digest tests:
  isolated settings supply no real Notificator object, and SQLite has no MySQL
  GET_LOCK. These are not reported as passing or solved. MySQL concurrency and
  migration rehearsal remain necessary before deployment.

## Canonical Notification Targets

`GET /users/me/native-push/events/<event_id>` resolves only an event owned by the
authenticated account in its current space. No query parameters are accepted.
Missing/foreign events share a 404 response. Successful responses contain only
canonical account/space/event identities and a typed navigation target, never
message content or arbitrary URLs. Current membership, friendship, visibility,
submission state and feature gates are checked; unavailable content returns an
unavailable target. Reading does not mark the event read. Responses are private
and no-store. Pages must independently authorize subsequent data requests.

All 16 target tests and the combined 143-test native push/account/topic regression
passed. `manage.py check --settings=Sermo.isolated_test_settings` passed with the
existing initialization warning. No new migration. Endpoint is development-only;
deployment requires separate confirmation.

## Next Gates

1. Authenticated binding endpoints are implemented on the development branch;
   deployment and client integration remain pending. No endpoint is live yet.
2. Connect the tested adapter/queue to automatic event dispatch behind an explicit
   disabled-by-default server gate. Configure credentials only after approval;
   confirm operational monitoring and credential rotation before enabling it.
3. iOS OS-permission/device-token lifecycle, account-specific opt-in, rotation,
   logout cleanup and canonical access-checked notification tap routing.
4. MySQL concurrency, migration rehearsal and signed-device sandbox/production
   delivery acceptance. The deferred Chronelle association is unrelated and remains
   untouched.

Worktree: `sermo-backend-native-push`, branch `codex/native-push`, based on remote
master `8ff7da5`. The original backend worktree and its two unpublished history
management commits are untouched. GitHub Actions workflows and repository hooks
were read-only checked and empty; no default-branch update, production command or
production migration is authorized by this development milestone.
