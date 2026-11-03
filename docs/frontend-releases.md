# Frontend release registry

Only frontend releases are recorded. Backend deployments, API builds and worker
revisions do not create user-facing updates.

- Public `GET /frontend/releases`: one no-store API response containing the four
  known origins, their deployed frontend IDs and bilingual release notes. It reads
  the database and never requests any site, static release file or service worker.
- Platform-admin `PUT /platform-admin/frontend/releases`: JSON
  `{ "kind":"frontend", "origin":"https://frienden.jyonn.space", "release": <release-notes.json> }`.
  Existing platform administrator authentication is required. Notes are immutable
  per ID; deployment origins are allowlisted and cannot silently go backwards.
- Deployment command: pipe the exact deployed `release-notes.json` to
  `manage.py publish_frontend_release --origin <known-origin>`. It uses the same
  validator and transaction as PUT. The frontend deployment scripts use existing
  SSH authorization so no admin token or new publishing secret is stored.

Publishing sequence: build → deploy files → verify release marker, HTML release
meta and entry script → register release notes → verify the public API. Pages
deployments must finish before registration; a Git push alone is insufficient.
The mirror scripts register each mirror, and the Frienden Pages publisher registers
both Pages origins. Failed registration is an incomplete release and must be retried.

The client polls this API while active, at startup, after returning to the foreground
and every three minutes. New releases produce one toast per version and shared red
dots; polling never activates a worker or navigates/reloads. The user must enter
Check for updates and confirm before update activation. The worker script is stable
across normal frontend releases and has no generated frontend-version dependency.

Legacy clients need one manual update to adopt this behavior. A compatibility
GET_RELEASE worker message reads the central API only when an old client explicitly
requests an upgrade. Existing offline media/avatar caches are preserved.
