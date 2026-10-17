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
python manage.py test User.tests_oauth_registry User.tests_oauth User.tests_oauth_avatar User.tests_oauth_migration --settings=Sermo.isolated_settings --noinput
python manage.py check --settings=Sermo.isolated_settings
```

29 tests run: ownership, separate admin JWT, stale versions, rejection, pending
revision isolation, exact redirects, loopback approval, disable/restore and exchange,
atomic/idempotent seed conflicts, grant preservation, incremental email and truthful
verification, PKCE/code reuse, scoped userinfo and existing avatar behavior. All 14
preexisting model/HTTP/PKCE/avatar tests remain and pass; 11 registry tests, two
additional legacy-client matrix tests and two migration/concurrency tests extend them.
The old three static-AST registration tests were replaced by database-backed exact
registration/scope checks, rather than preserving tests of the removed hardcoding.
The matrix independently exercises StillFolio, Chronelle and Supervision-local:
metadata, authorization, state return, wrong-client code exchange, successful exchange,
exact granted scopes, stable subject, absence of email, one-time code and revocation.
SQLite passes 28 and skips only the engine-dependent concurrent-lock test.
Installed MySQL 9.6.0 passes all 29 using a fresh `/tmp` datadir, no-defaults startup,
TCP/X Protocol disabled, and only a dedicated Unix socket. No deployment config,
production database, secrets, or extra software were used. Full migration history
applied on the empty MySQL database; 0082→0083→0082→0083 preserved synthetic existing
OAuthGrant rows byte-for-byte at ORM field level. Two concurrent writes produced one
success, one 409 and exactly one mutation audit. This verifies engine behavior locally,
not equivalence with the unknown production MySQL version/configuration.
Frontend callback tests: 2 passed. `npm run build` passed, including style checks.

Known baseline checks: `makemigrations --check --dry-run` reports existing Chat index
renames and OAuthGrant model-state normalization; 0083 intentionally excludes these
unrelated alterations. `npm run i18n:check` fails on existing missing Spanish keys and
existing lab-page hardcoded strings, reproduced from HEAD; new keys exist in every
locale and Chinese/English copy is provided. Other locales use English fallback copy.
Chrome extension UI blocks automated visual inspection. Mobile/desktop/dark-mode,
PWA drawer navigation, loading/empty/error screens still need browser acceptance.
The production version and physical backup/restore procedure remain unverified.

### Release gates versus baseline warnings

- **Release gate:** user approval for push/deployment, production schema changes and
  seeding has not been granted. Do not deploy on the strength of local tests.
- **Release gate:** confirm actual production MySQL and Django versions/configuration;
  the available local rehearsal used MySQL 9.6.0 and Django 6.1.2. Rehearse on matching
  versions if different, especially before any destructive schema reversal.
- **Release gate:** confirm StillFolio's actual callback and the original scopes/grants;
  verify an operator backup and recovery plan before migration. No live inventory was
  read in this task. Seed must stop on any mismatch.
- **Release gate:** browser acceptance of mobile/desktop/dark-mode/PWA and error/loading
  paths is incomplete because Chrome automation is blocked by an extension UI.
- **Not an 0083 SQL blocker:** existing model-state drift for OAuthGrant and Chat index
  names also exists on the previous committed baseline; do not run `makemigrations`
  automatically during release or include its suggested changes in this migration.
  An empty MySQL migration history and 0083 forward/reverse exercise both passed.
- **Not a runtime OAuth blocker:** existing i18n-check failures reproduce from HEAD;
  this change adds matching keys to all locales and Chinese/English content. If CI
  requires that checker to be green, resolve/accept its baseline separately before
  release; do not silently bypass a required CI gate.

## Exact changes requiring production confirmation

Schema adds `User_oauthapplication`, `User_oauthapplicationrevision` and
`User_oauthapplicationaudit` only. The application stores unique client ID, nullable
developer owner, enabled flag, version and approved revision pointer. Revision stores
display metadata, exact redirect lists, scopes, loopback exceptions and review state.
Audit stores actor/action/version/revision/time. No OAuthGrant column, token, scope or
existing account is rewritten. Seed imports exactly the three rows in the table above,
plus their approved revisions and import audit rows, and does not assign developers
ownership of the legacy apps. Identical rows are accepted idempotently; conflicting
registry rows or legacy grants abort the complete seed transaction.

Supervision email must be a **separate approved change**, not part of migration/seed:

```
client_id: supervision-local                         (unchanged)
redirect_uris: [http://127.0.0.1:8001/api/auth/friendom/callback/] (unchanged)
loopback_uris: [http://127.0.0.1:8001/api/auth/friendom/callback/] (unchanged)
scopes: [profile] -> [email, profile]
```

Create a revision, submit it, then review with `action=approve`, current application
`version`, that `revision_id`, and `approved_loopback_uris` equal to the exact loopback
list. A platform-admin JWT is required for approval; a developer JWT cannot approve.
Only after this registry approval may Supervision request `scope=profile email` and
the user expressly consent. Neither step replaces the other. Old grants keep profile
only. StillFolio and Chronelle keep their exact original scopes, with no email added.

### API and user impact

- Existing `/users/oauth/authorize`, `/token`, `/userinfo`, `/revoke` retain their
  request contracts. Their client policy comes from the registry. If a client is
  disabled or the registry is not seeded, access is denied, including old token reads.
- New metadata endpoint is required by the new consent frontend; deploying that
  frontend against an old backend breaks consent. Coordinate both versions.
- Three existing clients keep the same callbacks/scopes and their valid grants; no
  forced reauthorization or avatar-choice change is introduced by registry import.
- Logged-in users gain an application-management drawer for their own applications;
  separate platform admins gain creation/review/disable/restore and change history.
- Email fields appear only on newly consented email grants. Email requests before
  Supervision registry approval fail `invalid_scope`. Normal profile responses keep
  their old fields, and login/session/account merge behavior is unchanged.
- Pause OAuth authorize/token/userinfo/revoke traffic during the registry switchover;
  it is an OAuth availability window, not a planned general Sermo login shutdown.
  Plan maintenance messaging and keep non-OAuth traffic running where deployment permits.

## Proposed production procedure — requires separate explicit approval

1. Review both repository commits and finish browser acceptance. Confirm deployment
   engine/version and rehearse schema migration/rollback with synthetic data on it.
2. Confirm the three exact live client IDs, callback URLs (especially StillFolio's
   environment override), scopes and active grant inventory. A conflict stops import;
   never broaden scopes or overwrite records to make an import pass.
3. Take an operator-managed consistent database backup including migration history,
   original OAuthGrant rows, and registry/audit tables if already present. Record
   grant/client counts, exact callbacks/scopes and previous backend/frontend revisions;
   verify the backup is restorable on an isolated target, without exporting secrets into
   this workspace. Keep old code/assets available. Pause OAuth endpoints during backend transition:
   database-backed authorization requires both schema and seed records before traffic
   resumes. Apply schema migration 0083, then explicitly run:
   `python manage.py seed_oauth_applications --stillfolio-redirect-uri <confirmed-existing-uri>`.
   Inspect `migrate --plan` first and target the reviewed 0083 schema migration; do not
   let unrelated generated migrations enter the release. Seed validates grants/records and inserts only missing legacy registrations in one
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

Pause OAuth traffic, restore previous frontend/backend code together (backend baseline
`73b2edf`, frontend baseline `6b8bb95` for this task), keep additive
registry tables and audit records intact, and resume only after legacy-client checks.
Existing OAuthGrant records are unchanged, so previous code can still consume original
legacy grants. Newly registered clients and email scope will be unavailable on old code;
communicate that interruption before rollback. Do not drop registry tables to roll back
an incident. Schema reversal to 0082 is destructive to registry records and audits and
needs separate approval plus backup/export; it is not part of this task. No reverse
migration or production rollback has been executed on production. The reverse migration
was exercised only with synthetic rows on local SQLite/MySQL. If a database restore is
actually required, keep traffic paused, restore the validated backup under operator
control, verify migration history and grant/client inventories, and only then resume
legacy OAuth smoke checks. Physical backup restoration has not been rehearsed here.
