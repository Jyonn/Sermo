# Frienden OAuth application registry — local implementation

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
- Navigation is inside the existing Frienden router. Developer management is a
  `SideDrawer` under `/app/menu` → More apps, within the existing `AppChrome` and user
  authentication. Admin review is a section of the existing `/admin` platform-console
  layout and existing platform-admin authentication. A same-tab menu link to
  `/admin?section=oauth` is shown only when an existing platform-admin session is present.
  There is no new website or authentication system. The extra browser tab was only a
  temporary synthetic UI fixture; its files were removed and server stopped. Ordinary
  users cannot see the admin menu link or pass the admin API JWT checks.
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

30 tests run: ownership, separate admin JWT, stale versions, rejection, pending
revision isolation, exact redirects, loopback approval, disable/restore and exchange,
atomic/idempotent seed conflicts, grant preservation, incremental email and truthful
verification, PKCE/code reuse, scoped userinfo and existing avatar behavior. All 14
preexisting model/HTTP/PKCE/avatar tests remain and pass; 11 registry tests, two
additional legacy-client matrix tests and three migration/concurrency/staged-cutover tests extend them.
The old three static-AST registration tests were replaced by database-backed exact
registration/scope checks, rather than preserving tests of the removed hardcoding.
The matrix independently exercises StillFolio, Chronelle and Supervision-local:
metadata, authorization, state return, wrong-client code exchange, successful exchange,
exact granted scopes, stable subject, absence of email, one-time code and revocation.
SQLite passes 29 and skips only the engine-dependent concurrent-lock test.
Installed MySQL 9.6.0 passes all 30 using a fresh `/tmp` datadir, no-defaults startup,
TCP/X Protocol disabled, and only a dedicated Unix socket. No deployment config,
production database, secrets, or extra software were used. Full migration history
applied on the empty MySQL database; 0082→0083→0082→0083 preserved synthetic existing
OAuthGrant rows byte-for-byte at ORM field level. Two concurrent writes produced one
success, one 409 and exactly one mutation audit. The staged test runs the frozen actual
`73b2edf` OAuth module from `User/fixtures/oauth_legacy_73b2edf.txt` before/after DDL,
continues serving through the old runtime until seed completes, and only then switches
to new enforcement. It also proves an early switch fails and that old userinfo can
bypass disable policy for both a legacy and newly registered client, so it is rejected
as a rollback destination after policy use. This verifies engine behavior locally,
not version-equivalence with production MySQL 8.0.46 / Django 5.1.12.
Frontend callback tests: 2 passed. `npm run build` passed, including style checks.

Known baseline checks: `makemigrations --check --dry-run` reports existing Chat index
renames and OAuthGrant model-state normalization; 0083 intentionally excludes these
unrelated alterations. `npm run i18n:check` fails on existing missing Spanish keys and
existing lab-page hardcoded strings, reproduced from HEAD; new keys exist in every
locale and Chinese/English copy is provided. Other locales use English fallback copy.
Supported Chrome automation subsequently verified the synthetic shared management
component at desktop/light and 390px/dark sizes, with DOM scroll width equal to 390px
and no horizontal overflow. The exact-loopback checkbox changed Approve from disabled
to enabled, without submitting any mutation. A later extension UI block prevented
empty/error/loading-state and integrated PWA drawer acceptance; it was not bypassed.
Synthetic SQL backup/restore is now verified below; production backup recoverability
and exact production-version rehearsal remain unverified.

### Synthetic backup/restore rehearsal

`scripts/rehearse_oauth_backup.py` refuses settings other than the named synthetic
settings, non-`/tmp/sermo-oauth-mysql.*` sockets, nonempty source databases, non-root or
nonempty credentials, and any database name other than `oauth_rehearsal`. It uses the
already installed MySQL client/dump tools with `--no-defaults`. No deployment file or
production connection is accepted. All identities and grant hashes are generated locally.

- Before 0083: backup restored into `oauth_restore_before_registry`; legacy grant,
  user, space and migration-history rows compared exactly; registry tables absent.
- After 0083 and seed: backup restored into `oauth_restore_after_registry`; the same
  four tables plus applications/revisions/audits compared exactly. Original three
  grants remained unchanged. A new synthetic client and a disabled legacy client were
  retained with disabled flags after restore; no policy was reopened by restoration.
- No real grant/token/secret was read or copied. Dumps stay in the disposable `/tmp`
  datadir. This is a MySQL 9.6 logical-backup rehearsal with synthetic data, not proof of
  production snapshots, MySQL 8.0 recovery, large-database timing or point-in-time recovery.

### Exact environment and UI blockers

Read-only local inventory found MySQL 9.6 and Python 3.14 (test venv), plus Python 3.11,
but no Python 3.12/MySQL 8.0 or Docker, Podman, Colima, Lima, nerdctl, OrbStack runtime.
There is no installed compatible container runner in which to use an official matching
image. No new software or network/security changes were attempted. Matching-version
execution remains **not run**, rather than being relabeled as passed by the 9.6 tests.

The previous ephemeral QA tab is closed. The browser inventory now contains user and
other-task tabs, not the OAuth fixture; none were repurposed and no additional tab was
opened. Full PWA/empty/error/loading acceptance remains **not run** pending a permitted
existing local app test surface or user-selected session. Prior desktop/light and
mobile/dark component observations remain valid only for their synthetic fixture.

### Reviewable release checklist

| Item | Evidence/status |
| --- | --- |
| Three legacy callbacks/scopes preserved, no implicit email | PASS: legacy matrix, seed checks, read-only deployed configuration |
| All old model/HTTP/PKCE/avatar tests | PASS: original 14 included in suite |
| New permissions, owner isolation, audit, stale versions | PASS: registry suite |
| Migration, code/token preservation, true concurrent writes | PASS on local MySQL 9.6; lock test skipped on SQLite |
| Add tables → seed → backend switch → frontend dependency order | PASS as synthetic old/new runtime sequence; actual process/proxy rollout NOT RUN |
| Early backend switch against empty registry | Expected denial demonstrated; forbidden rollout order |
| SQL backup/restore before and after registry | PASS with synthetic data on local MySQL 9.6; no production backup/restore |
| Disable policy after restore | PASS: restored flags match exactly; compatible runtime enforcement tests pass |
| Rollback after custom-client approval/disable use | Old backend shown unsafe; use compatible backend or keep new backend and revert frontend |
| Frontend callback tests and production build | PASS: 2 tests; latest frontend build passes |
| Desktop/light, mobile/dark shared component | PASS within synthetic fixture; no 390px horizontal overflow |
| Integrated PWA and empty/error/loading UI | NOT RUN: no available OAuth QA tab; other tabs left untouched |
| Exact MySQL 8.0.46 / Python 3.12.3 / Django 5.1.12 | NOT RUN: matching installed environment/container runtime absent |
| Production DDL/seed/backup/switch/email approval/push/mirror | NOT RUN: requires separate user authorization and operational preflight |
| i18n/migration drift full checks | Existing baseline warnings; required CI gates still must be resolved or explicitly accepted |

### Read-only production observations (2026-10-17)

- Deployed checkout: `73b2edf7f099e2c7020cd260f8d26970d6002696`; tracked files clean.
- Deployment virtual environment: Python `3.12.3`, Django `5.1.12`.
- Database `SELECT VERSION(), @@version_comment`: MySQL
  `8.0.46-0ubuntu0.24.04.4` (Ubuntu). No grant rows or tokens were queried/exported.
- Public main site and mirror both report release `2026.10.17.16`.
- Matching Sermo processes' allowlisted callback environment was evaluated against
  deployed registration source. All observed processes agree on the three callbacks
  and scopes in the seed table; StillFolio uses the displayed default callback.
  This checks deployed source/configuration, not an authenticated grant roundtrip or
  introspection of already-loaded worker Python functions. Some worker executable
  symlinks refer to a deleted Python binary; the listed patch version is the currently
  installed deployment interpreter, not an independently measured running-worker patch.
- Local installed tools provide MySQL 9.6 / Django 6.1.2, not the production versions.
  No new software was installed. Existing same-family rehearsal passes, but a matching
  8.0/5.1 rehearsal has not been completed and no production DDL has been exercised.

These probes were read-only: no service reload, endpoint pause, production backup,
migration, seed, application approval, token creation, push or deployment was performed.
Existing server credentials were used internally by the standard MySQL client; their
contents and all non-allowlisted process environment values were not emitted.
Display branding is Frienden. Historical `friendom` inside the approved Supervision
callback is an unchanged protocol URL, not a display-name correction target.

### Release gates versus baseline warnings

- **Release gate:** user approval for push/deployment, production schema changes and
  seeding has not been granted. Do not deploy on the strength of local tests.
- **Release gate:** versions and callbacks are now observed above; decide whether to
  require a matching MySQL 8.0 / Django 5.1 rehearsal before approved production DDL.
  Local 9.6/6.1 success alone is not proof of version-equivalent behavior.
- **Release gate:** verify an operator backup and recovery plan and validate grant
  compatibility via the approved seed. Live grant inventory has not been read or copied.
  Seed must stop on any mismatch; it is not authorized to repair records automatically.
- **Acceptance gap:** desktop/light and mobile/dark shared component checks passed;
  full PWA navigation and loading/empty/error acceptance remain blocked by browser UI.
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
- A planned OAuth pause is not inherently required by the additive schema: pre-create
  and seed before the new backend serves traffic. A single-step in-place replacement
  without preparation does require an OAuth maintenance window to avoid an empty
  registry. DDL metadata locking, worker reloads and failed health checks may still
  cause a brief OAuth interruption; duration cannot be estimated responsibly without
  live schema/traffic/reload measurements. Do not promise zero downtime or a time bound.

## Proposed production procedure — requires separate explicit approval

1. Review the local commits and remaining acceptance gaps. Choose staged rollout below
   or explicitly approve an OAuth maintenance window. Production versions are listed
   above; matching-version rehearsal and backup restoration are still not proven.
2. Confirm the three exact live client IDs, callback URLs (especially StillFolio's
   environment override), scopes and active grant inventory. A conflict stops import;
   never broaden scopes or overwrite records to make an import pass.
3. Take an operator-managed consistent database backup including migration history,
   original OAuthGrant rows, and registry/audit tables if already present. Record
   grant/client counts, exact callbacks/scopes and previous backend/frontend revisions;
   verify the backup is restorable on an isolated target, without exporting secrets into
   this workspace. Keep old code/assets available. From a separate prepared release
   directory, with existing workers still serving their unchanged old source tree,
   apply schema migration 0083 and explicitly run:
   `python manage.py seed_oauth_applications --stillfolio-redirect-uri <confirmed-existing-uri>`.
   Inspect `migrate --plan` first and target the reviewed 0083 schema migration; do not
   let unrelated generated migrations enter the release. Seed validates grants/records and inserts only missing legacy registrations in one
   transaction; it never edits grants. Repeated identical imports do nothing.
4. Verify three registered clients and unchanged grants before new workers receive
   traffic. Switch backend, validate metadata/legacy OAuth, then publish frontend and
   mirror. Old frontend remains compatible with the three original clients while the
   prepared new backend runs. No new clients, email approvals or disabling operations
   should be enabled during mixed-worker transition. Publish mirror only through
   `npm run deploy:mirror`, then verify its release ID. No such operation is authorized
   by the present read-only preflight.
5. Separately approve any Supervision scope expansion with an exact diff:
   `['profile'] -> ['profile', 'email']`, same client ID and exact loopback callback.
   Submit/review a registry revision with explicit loopback confirmation. Existing
   grants stay profile-only. Each user must explicitly consent to a new `profile email`
   request before Supervision consumes a verified address for its Gravatar hash.

### Staged rollout feasibility

The code permits an additive expand-then-switch rollout: old runtime ignores the new
tables, old workers continue writing the same valid grants during seeding, and a fully
seeded registry accepts those grants when new workers replace old ones. Keep old source
directories immutable: placing new files directly over the live checkout can make old
workers lazily import new modules before the registry is ready. A separate release
directory and controlled worker/proxy switch are operational prerequisites, not changes
performed by this task. Verify the active API launch/reload arrangement before using
this plan; current checks did not establish a blue/green or graceful-reload mechanism.

After all new workers are healthy, remove old workers, release the frontend, and only
then open new-client/email approval and disable/restore operations. Mixed old/new workers
cannot consistently enforce new registration or disable decisions. If that operational
isolation cannot be arranged, choose a short explicitly approved OAuth maintenance
window instead of pretending the in-place rollout is safe. Ordinary Sermo sessions
are not intentionally invalidated; metadata locks or a shared-process restart could
still affect other requests. The authorization for either switch strategy remains pending.

## Rollback proposal

Pause OAuth traffic, restore previous frontend/backend code together (backend baseline
`73b2edf`, frontend baseline `6b8bb95` for this task), keep additive
registry tables and audit records intact, and resume only after legacy-client checks.
Existing OAuthGrant records are unchanged, so previous code can still consume original
legacy grants during an initial legacy-only rollout. Do not treat this as an unrestricted
rollback after management features have been used: the old backend ignores registry
disable flags and does not emit email fields, and its userinfo/revoke paths may still
accept already-issued custom-client tokens while new authorization/code exchange for
those clients fails. A rollback then requires a policy-compatible backend or a separately
approved plan to contain/revoke affected grants; this task has not authorized revocation.
Prefer retaining the new backend and rolling back only the frontend where viable.
Communicate the actual affected clients before rollback. Do not drop registry tables to roll back
an incident. Schema reversal to 0082 is destructive to registry records and audits and
needs separate approval plus backup/export; it is not part of this task. No reverse
migration or production rollback has been executed on production. The reverse migration
was exercised only with synthetic rows on local SQLite/MySQL. If a database restore is
actually required, keep traffic paused and use a separately approved recovery plan.
After management has been used, never restore the pre-registry or a stale pre-disable
snapshot and blindly rerun the legacy seed: doing so can lose disable/review/audit
decisions and reopen access. Preserve or reconcile all current disable and approval
decisions under a policy-compatible backend before any traffic resumes; a backup is
not an authorization to discard later security decisions. If that reconciliation cannot
be proven, remain paused rather than serve through old permissive enforcement.
Verify migration history, client/grant inventories, latest disabled state and audits
on the recovery target. Synthetic logical SQL restoration was rehearsed locally;
production physical/PITR recovery and latest-policy reconciliation were not performed.
