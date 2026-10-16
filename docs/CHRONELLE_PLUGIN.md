# Chronelle public calendar plugin

The plugin uses Chronelle's PKCE application authorization and public iframe
flows, documented in Chronelle's `docs/INTEGRATIONS.md`. Users authorize on
Chronelle; Sermo claims the result server-side and binds the returned calendar.
Friends and strangers see only the calendar's public schedules.

- `POST /users/me/chronelle/authorize`: authenticated; creates or resumes a
  ten-minute authorization request and returns the Chronelle authorization URL.
- `POST /users/me/chronelle/claim`: authenticated; polls at least five seconds
  apart while Sermo is visible. Returns pending, approved, denied or expired.
- No redirect URI is required: users approve in the new tab and return to Sermo.
  If popups are blocked, the same-tab fallback resumes polling after browser Back.
- `DELETE /users/me/chronelle`: authenticated; removes only the caller's binding.
- Public profile and current-user responses include `chronelle_calendar_id`.
- The server generates a random verifier and S256 challenge, checks the state,
  and stores pending secrets in a private per-user database field, not browser
  storage or process-local memory. Claims are serialized and throttled. Pending
  state expires and is cleared on completion, denial, expiry or unlinking.
- Only the calendar UUID is public. The returned private access key is discarded,
  never persisted or included in profile responses or iframe URLs. Selecting
  private groups on Chronelle does not make them visible on Sermo profiles.
- The shared frontend component builds `/embed/#/share/UUID` and follows Sermo's
  resolved theme. It uses lazy loading, no-referrer and a read-only sandbox.
- Unbinding affects Sermo only; Chronelle remains the source of truth for
  event visibility, deletion and public access.

Deploy backend migration `python manage.py migrate` before releasing the frontend.
Migrations 0081 and 0082 add the binding and private authorization state fields.
No changes to Chronelle are required, provided `/embed/` permits Sermo framing.

Sermo unlinking does not revoke the independent Chronelle application grant;
users can revoke it in Chronelle's sharing/access settings. The integration
protocol currently has no third-party grant-revocation endpoint.

Private application keys from Chronelle's PKCE flow must not be published in
profile responses or iframe URLs visible to other users. If private schedules
are needed later, design a separate audience-scoped server-side projection.
