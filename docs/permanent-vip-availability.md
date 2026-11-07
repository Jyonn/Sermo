# Permanent VIP Availability Patch

## Policy

`PermanentVipCampaign.space_activity_open` is shared by status reporting and new
claims. It checks the exact campaign, enabled state, space assignment, start and
end times. Automatic assignment is limited to this campaign. Missing manual
assignment returns closed instead of raising a lookup error.

Claims lock the global counter and reload the current user under a row lock.
New claims also lock the campaign/space run while checking availability. The
shared requirements helper evaluates verified contacts and the next slot's level.
Slot allocation, resource inventory, VIP flag and the once-only growth event are
one transaction. Growth response notifications are recorded after that transaction.

Existing inventory ownership still permits idempotent bundle repair after the
campaign closes, without allocating another slot. Historical slot metadata can be
greater than today's counter; no historical counter backfill is attempted.

## Verification

- 13 focused tests: missing/disabled/unassigned/future/expired/other-space activity,
  automatic assignment isolation, duplicate claims, current contact state, level
  boundaries, growth-failure rollback, legacy bundle repair and direct HTTP denial.
- 67 related VIP/growth/activity/Explorer tests passed on isolated SQLite.
- `manage.py check --settings=Sermo.isolated_test_settings` passed. The existing
  app-initialization database-query warning remains, not introduced by this patch.
- English and Chinese catalogs compiled successfully.
- The isolated settings now use the production Shanghai timezone. One historical
  starry-night fixture now pins its event clock instead of depending on today's
  date; no production date algorithm was changed.
- `makemigrations --check --dry-run` is NOT clean: the same Chat index renames and
  OAuthGrant options/ID drift were reproduced on unchanged baseline `3b9bda6` in a
  temporary worktree. This patch changes no model fields/options and introduces
  no migration. The unrelated drift is not repaired here.
- MySQL row-lock/concurrency behavior is NOT certified by SQLite. A read-only
  local MySQL socket probe was denied for the uncredentialed root account; no
  business data, credentials, database permissions or service configuration were
  read or changed. Matching production-engine concurrency validation remains open.

## Release Boundary

This patch is developed on `codex/native-push`; do not deploy that entire branch.
Only the reviewed VIP policy commit may be considered, after explicit approval
and comparison with current production files. It does not authorize APNs/native
push, keys, migrations or account changes. Verify the deployed growth helper
supports `notify=False` and `record_growth_award` before applying the isolated
policy diff. Do not overwrite unrelated server code or translation updates.

No production deployment, claim or data migration was performed. User-facing web
release notes and mirror synchronization belong to the approved release, not an
unreleased local patch.
