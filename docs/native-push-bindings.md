# Native Push Binding Foundation

Development only. Deployment requires separate user confirmation. The current
change adds a model, migration and authenticated binding endpoints. None is live;
it does not enqueue or deliver any notification.

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
never returned. `delivery_available` remains false until delivery is implemented.

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

## Next Gates

1. Authenticated binding endpoints are implemented on the development branch;
   deployment and client integration remain pending. No endpoint is live yet.
2. Native delivery route through the existing notification topic policy, not an
   independent topic matrix. Add deduplication, bounded attempts and token failure
   handling; configure APNs credentials only on the server after approval.
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
