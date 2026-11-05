# Private birthday celebrations

Birthday settings are separate from `/users/me` and public member/card payloads. Store only month/day; no birth year or age. Business dates use `Asia/Shanghai`. February 29 is observed on February 28 in non-leap years. A change is allowed after exactly 365 days from the last successful selection, enforced under a locked user row. Reporting and writes use the same policy in `User/birthday.py`.

## Member endpoints

- `GET /users/me/birthday`: private settings, next change time, server time, current celebration and letter envelopes.
- `POST /users/me/birthday`: `{month: 1..12, day: 1..31}` with actual calendar validation; always updates the authenticated member, ignoring any recipient ID.
- `POST /users/me/birthday/seen`: acknowledges this year's active welcome; no change outside the birthday day.
- `GET /users/me/birthday/letters`: private archive of up to 50 delivered letters; no text until unsealed and no future envelopes.
- `POST /users/me/birthday/letters/<id>/open`: returns letter text only to its recipient, on or after its stored occasion date; otherwise 404. Marks opening idempotently. Letters remain readable after the skin expires.

Status includes `server_now`, `can_change`, `available_at`, `active`, `celebration_id`, `ends_at`, `auto_open` and `letters`. Envelopes include title, sender display name, role, opening and update timestamps, never letter text or administrator email. Outside the birthday day the envelope list is empty. No sharing, transfer, claim or gift endpoints exist.

## Administrator letters

- Space administrator: `GET/POST /spaces/admin/users/<id>/birthday-letter`, with a space access token and mandatory same-space enforcement.
- Platform administrator: `GET/POST /platform-admin/members/<id>/birthday-letter`, with a platform-admin access token.

GET resolves the next observed birthday, including today, and returns the current role's draft. POST accepts `birthday_year`, `title` (1–80 characters) and `body` (1–3000 characters). The submitted year must still match the next occasion, preventing stale editors from writing to a different year. Each role has one independently editable letter per recipient/year. Identical saves are no-ops; changed read letters become unread. Save operations are audited; author emails stay internal. Other role letters cannot be overwritten by supplying a source field.

## Client lifecycle

The birthday skin is `birthday-botanical`, personal and automatically expires; it takes precedence over the space skin only on authenticated app pages. Expiry uses server-relative duration, avoiding loops caused by a client clock ahead of the server. Foreground and periodic status refreshes discover new letters. Server acknowledgement plus a session fallback prevents repeated automatic welcomes; users can reopen from birthday banners or personal information. Closing a reminder dismisses it for the current app session across Tabs.

The chosen theme is 花间来日. Its three bitmap resources are in `frienden/assets/themes/birthday-botanical` in the managed cloud library; frontend code stores stable URL metadata only. Lines/corners adapt using CSS geometry and separately positioned transparent ornaments. The full-screen welcome and its letter dialog use the shared focus/body-lock/overlay ordering system. No perpetual particles or background audio.

Migration: `User/0095_birthday.py`; adds nullable member fields and a new uniquely constrained `BirthdayLetter` table without rewriting existing birthdays or user data. Before publishing the frontend, apply backend migrations and verify the birthday endpoints.
