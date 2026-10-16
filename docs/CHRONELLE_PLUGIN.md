# Chronelle public calendar plugin

The plugin uses the public iframe flow in Chronelle's `docs/INTEGRATIONS.md`.
It does not implement account authorization or prove calendar ownership: users
attach an existing public share link to their profile, with explicit disclosure
that both friends and strangers can see the calendar.

- `POST /users/me/chronelle`: authenticated; JSON `{ "url": "https://chronelle.jyonn.space/#/share/UUID" }`.
- `DELETE /users/me/chronelle`: authenticated; removes only the caller's binding.
- Public profile and current-user responses include `chronelle_calendar_id`.
- The server stores only the calendar UUID. Access keys, query parameters,
  foreign hosts, credentials and non-HTTPS links are rejected.
- The shared frontend component builds `/embed/#/share/UUID` and follows Sermo's
  resolved theme. It uses lazy loading, no-referrer and a read-only sandbox.
- Unbinding affects Sermo only; Chronelle remains the source of truth for
  event visibility, deletion and public access.

Deploy backend migration `python manage.py migrate` before releasing the frontend.
Migration 0081 adds an empty-by-default field without rewriting existing accounts.
No changes to Chronelle are required, provided `/embed/` permits Sermo framing.

Private application keys from Chronelle's PKCE flow must not be published in
profile responses or iframe URLs visible to other users. If private schedules
are needed later, design a separate audience-scoped server-side projection.
