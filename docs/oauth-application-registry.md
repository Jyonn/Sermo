# OAuth application registry — local implementation

Status: implemented locally; no production migration, seeding, approval, grant creation,
push, or deployment performed. Frontend draft release ID: `2026.10.17.17`.

## Changes

- `User/migrations/0083_oauth_application_registry.py` adds application, revision and
  audit tables plus approved-revision pointer. It does not modify OAuthGrant.
- `User/oauth_registry.py` supplies shared redirect/scope policy, ownership, immutable
  submitted configurations, revision transitions, audit writes and version conflicts.
- `/users/oauth/applications` GET/POST and `/<id>` POST use ordinary user JWTs.
  Users see and edit only owned drafts, create revisions and submit for review.
- `/platform-admin/oauth/applications` uses the separate platform-admin JWT type.
  Admins directly create approved records, review pending revisions, disable/restore.
  Exact HTTP loopback exceptions require explicit approval. Rejection needs a note.
- All mutations include `version` except creation; edits/reviews also include
  `revision_id`. Stale writes return 409. Mutations/audits share a transaction.
- Pending/rejected revisions leave the approved pointer unchanged. Disabling blocks
  authorization, code exchange and userinfo. Restoring preserves unexpired grants.
- `/users/oauth/metadata` validates the complete authorization request and returns
  approved display metadata and requested scopes. Consent no longer trusts client
  names, callback URLs or permissions from a frontend registration table.
- PKCE S256, state, one-time codes, expiry, user consent and grant-specific scopes
  remain. Previously issued codes/tokens survive approved callback/scope changes;
  codes retain their original exact callback and PKCE checks.

## Email consumer contract

`email` is independent from `profile`. Only a grant containing `email` gets
`email: string | null` and `email_verified: boolean`. The flag is true only when a
nonempty stored address and `User.email_verified_at` both exist. Unverified addresses
are returned with false; missing addresses return null/false. No account linking,
merging, inbox access, login-email change or avatar-selection change is introduced.
`sub` is unchanged. Scope approval is separate from each user's consent.

The three legacy seeds remain exactly:

| Client | Redirect | Scope |
| --- | --- | --- |
| stillfolio | explicit existing deployment callback supplied to seed command | phone profile vip |
| chronelle | https://chronelle.jyonn.space/api/profile/sermo/callback/ | profile |
| supervision-local | http://127.0.0.1:8001/api/auth/friendom/callback/ | profile |

The default legacy StillFolio callback is
`https://stillfolio.jyonn.space/auth/sermo/callback`; its historical environment override
must be confirmed by the operator before import. This work did not inspect secrets.

## Isolated verification

Use `/tmp/frienden-oauth-tests/bin/python` in this workstation (existing temporary
environment, Django 6.1.2). `Sermo.isolated_settings` never imports deployment settings;
it redirects legacy direct `Sermo.settings` imports to synthetic settings and uses
only a temporary in-memory SQLite test database.

```
python manage.py test User.tests_oauth_registry User.tests_oauth User.tests_oauth_avatar --settings=Sermo.isolated_settings --noinput
python manage.py check --settings=Sermo.isolated_settings
```

25 tests passed: ownership, separate admin JWT, stale versions, rejection, pending
revision isolation, exact redirects, loopback approval, disable/restore and exchange,
atomic/idempotent seed conflicts, grant preservation, incremental email and truthful
verification, PKCE/code reuse, scoped userinfo and existing avatar behavior.
Frontend callback tests: 2 passed. `npm run build` passed, including style checks.

Known baseline checks: `makemigrations --check --dry-run` reports existing Chat index
renames and OAuthGrant model-state normalization; 0083 intentionally excludes these
unrelated alterations. `npm run i18n:check` fails on existing missing Spanish keys and
existing lab-page hardcoded strings, reproduced from HEAD; new keys exist in every
locale and Chinese/English copy is provided. Other locales use English fallback copy.
Chrome extension UI blocks automated visual inspection. Mobile/desktop/dark-mode,
PWA drawer navigation, loading/empty/error screens still need browser acceptance.
No production-engine concurrency or migration rehearsal was performed.

## Proposed production procedure — requires separate explicit approval

1. Review both repository commits and finish browser acceptance. Confirm deployment
   engine/version and rehearse schema migration/rollback with synthetic data on it.
2. Confirm the three exact live client IDs, callback URLs (especially StillFolio's
   environment override), scopes and active grant inventory. A conflict stops import;
   never broaden scopes or overwrite records to make an import pass.
3. Take an operator-managed backup. Pause OAuth endpoints during backend transition:
   database-backed authorization requires both schema and seed records before traffic
   resumes. Apply schema migration 0083, then explicitly run:
   `python manage.py seed_oauth_applications --stillfolio-redirect-uri <confirmed-existing-uri>`.
   Seed validates grants/records and inserts only missing legacy registrations in one
   transaction; it never edits grants. Repeated identical imports do nothing.
4. Verify three registered clients and unchanged grant counts/scopes. Deploy backend
   and frontend as a coordinated release; resume OAuth endpoints after metadata and
   legacy login checks. Publish mirror only through `npm run deploy:mirror`, then
   verify its release ID. No push/mirror operation is authorized by this local task.
5. Separately approve any Supervision scope expansion with an exact diff:
   `['profile'] -> ['profile', 'email']`, same client ID and exact loopback callback.
   Submit/review a registry revision with explicit loopback confirmation. Existing
   grants stay profile-only. Each user must explicitly consent to a new `profile email`
   request before Supervision consumes a verified address for its Gravatar hash.

## Rollback proposal

Pause OAuth traffic, restore previous frontend/backend code together, keep additive
registry tables and audit records intact, and resume only after legacy-client checks.
Existing OAuthGrant records are unchanged, so previous code can still consume original
legacy grants. Newly registered clients and email scope will be unavailable on old code;
communicate that interruption before rollback. Do not drop registry tables to roll back
an incident. Schema reversal to 0082 is destructive to registry records and audits and
needs separate approval plus backup/export; it is not part of this task. No reverse
migration, production rollback or production data modification has been executed.
