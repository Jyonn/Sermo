# Default-Off Native Push On The Drop Release

This development branch starts at `0c8fcbd`, not the older compatibility merge.
Read-only process metadata identified the running Sermo directory as
`/home/ubuntu/frienden-backend-releases/drop-diary-0c8fcbd`. All 114 retrieved
necessary source/migration files match that tracked baseline byte-for-byte.
Production source copies, credentials and configuration values are not committed.

## Patch Boundary

- Add only native binding/target endpoints, queue hook/models, bounded worker,
  APNs adapter, HTTP/2 dependency and translated native errors.
- Preserve existing VIP, Drop, voice, quick-action and Chronelle behavior.
  Existing User models differ only by the explicit native queue import/call and
  native model registrations; the candidate generator checks the full AST.
- Preserve every existing compiled English/Chinese catalog entry.
- Join native `0096_merge_native_push` and existing `0100_drop_connection` with
  operation-free `0101_merge_native_push_and_drop`. No applied migration changes.
- Provider, queue, worker and client/device enablement remain separate gates.
  This branch does not activate providers or change settings/capabilities.

## Validation

Under production-matched Django 5.1.12 and package versions, the exact tracked
candidate passes 120 MySQL tests including native row-lock races and Drop cases:
`/private/tmp/frienden-active-push-tracked-mysql.log`. The private candidate also
passes 112 SQLite cases and 120 MySQL cases. Fourteen mocked APNs adapter tests
pass in `/private/tmp/frienden-active-push-apns-tests.log`.

A new empty local schema first migrated only through User 0100 Drop; metadata
confirmed no native migration was applied. It then migrated forward through
native bindings/queue and the empty 0101 merge. `migrate --check` passes. Logs:
`/private/tmp/frienden-active-push-upgrade-base.log` and
`/private/tmp/frienden-active-push-upgrade-forward.log`. No migration was faked.
System/whitespace checks pass; the existing startup-query warning remains.
Temporary database and volume were shut down/unmounted after validation.

Local Python 3.11/MySQL 8.4 differ from production Python 3.12/MySQL 8.0. Tests
use synthetic data/mocked providers and do not prove actual APNs delivery or
whole-production equivalence. No production mutation or phone operation occurred.

## Rollout Boundary

The previous package for `/home/ubuntu/Sermo` is retired. No approval for it
overrides the changed-target stop condition. A new package must bind its
manifest to the actual running release directory, reject symlink paths, verify
all source/dependency hashes and absent new destinations, and require approval
for this candidate. Any changed working directory or input stops deployment.
Keep source backups and existing framework versions; never publish this entire
development worktree or test fixtures as a deployment package. No deployment has
occurred during this reconciliation.
