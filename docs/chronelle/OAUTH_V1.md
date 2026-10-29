# Chronelle OAuth v1

Official contract: https://chronelle.6-79.cn/oauth-guide.html
The old account-bindings/display-session endpoints are retired.

Run `python manage.py configure_chronelle` interactively on the backend host.
Key/hidden Secret are saved only to Config as CHRONELLE_CLIENT_ID and
CHRONELLE_CLIENT_SECRET. CHRONELLE_STORAGE_KEY is a dedicated Fernet key;
existing private-file encryption material is preserved when available.
Config takes precedence over legacy environment/private-file fallback.
Never put credentials in Git, frontend variables, logs or command arguments.
Run `python manage.py check_chronelle` to verify client authentication and all
four exact callbacks without granting access or reading user calendars.

Both Sermo and Frienden canonical/mirror origins use `/app/research` callbacks.
Mirror sites use chronelle.6-79.cn; canonical sites use chronelle.jyonn.space.
The backend seals random state and PKCE. The callback removes codes from the
URL and posts state/code to the authenticated backend for one-shot exchange.
Rotating refresh/exchange uncertainty is persisted before the remote request;
lost results are never replayed. Parent tokens remain sealed and server-only.
Old users are prompted to reconnect. No calendar data is deleted. Global group
visibility must be reconfirmed after a new binding.

The backend checks membership, visibility and current grant/version, then signs
five-minute HS256 child JWTs (no event content). `/oauth/embed/APP_KEY/` receives
only these tokens in memory through chronelle.calendar v2. Exact origin, source,
channel and request ID are checked. There is no cross-site-cookie dependency.
Own calendar is selected by default; peers remain hidden until confirming the
global busy/group policy. Incomplete/failed reads never mean an empty calendar.

Child JWTs are stateless and cannot be individually revoked. Privacy/membership
changes requiring immediate removal revoke relevant parent authority when live
child displays exist. This requires reconnecting and is explained in the UI.

Meeting invitations use stable verified subjects and persistent idempotency
keys at `/api/oauth/calendar/invitations/`. Peers accept/reject themselves;
the app never writes directly into another member's calendar. Uncertain delivery
retains the original command for retry. Deployment checks send no real meetings.
