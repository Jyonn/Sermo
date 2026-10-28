# Chronelle research integration

Full authorization is separate from the legacy read-only authorization. Existing
read-only links are preserved; they never become full credentials automatically.
Users must explicitly approve the fixed Chronelle full-binding v1 permission set.

## Backend configuration

Set `CHRONELLE_BINDING_CLIENT_ID`, `CHRONELLE_BINDING_CLIENT_SECRET` and
`CHRONELLE_BINDING_STORAGE_KEY` (a dedicated Fernet key), or use the protected
`~/.config/frienden/chronelle-binding.json` file with keys `client_id`,
`client_secret`, `storage_key`. The file must be mode 0600. A protected custom
path can be selected with `CHRONELLE_BINDING_CONFIG`. Never commit these values
or put them in frontend VITE variables. Retain the storage key with private
database backups; loss of the key requires reauthorization.

The client must register each of the four official HTTPS origins as a display
origin and each origin followed by `/app/research` as an exact redirect URI.
Mirror users use `chronelle.6-79.cn`; primary users use `chronelle.jyonn.space`.
Unknown origins cannot initiate a binding, issue projections or send invitations.

Access/refresh tokens and PKCE material are encrypted in `User.ChronelleAccount`.
Code claims and rotating refresh tokens are one-shot: uncertainty is persisted
before the remote call; lost results are not retried with consumed credentials.
The frontend receives only public display identifiers, not owner tokens or keys.

## Calendar and meeting policy

Research is gated by the existing space flag and current chat membership. Self
projections show own groups. Other members' groups default hidden, including new
groups. Each member sets their own project-specific details/busy lists. Policy
changes and member departures revoke outstanding project projections before
applying the change. Failed revocation is explicit rather than falsely claiming
access was removed. Display sessions expire after at most five minutes.

The iframe requires the designated viewer's Chronelle session. Safari/PWA cookie
restrictions can prevent embedding even after OAuth succeeds. Show login-required
or unknown availability; do not pass owner credentials to bypass this restriction.
Messages must match exact origin, source window, channel and current request ID.
Selection is a draft, not proof that hidden calendars are free.

Meetings use immutable bound subjects, resolved server-side from active project
members. They create Chronelle invitations as the authenticated organizer; peers
remain pending until they accept. The local command UUID and upstream idempotency
key prevent duplicate writes. Unknown delivery is retained for the same-command
retry, including after a page reload; it is never presented as successful.
The verified organizer and recipient subjects are frozen with the command. A
retry after switching a linked account fails closed instead of writing as a new
identity; those internal subject mappings are omitted from frontend responses.

## Upstream increment

Chronelle's pre-existing full-binding source tree contains unrelated uncommitted
work. The two adjacent patch artifacts preserve only this task's subject-recipient
increment and its tests/documentation; do not stage or replace the other work.
Apply with `apply_patch` from the Chronelle backend root when needed. The runtime
increment changes only `binding_api.py` and adds `binding_participants.py`; no
Chronelle schema migration is required. Its baseline is checked before deployment.

Local verification uses temporary databases and synthetic accounts only; no
production users, calendar events or invitations are created by the QA scripts.
