# Exact Friendship Status

Authenticated `GET /friends/status?user_id=<peer>` resolves the pair in the
current user's space. Self and cross-space targets remain rejected.

The existing contract is preserved: `is_friend` is true only for accepted
relations; `friendship` is included only for accepted relations.

Additive fields:

- `relationship_resolved`: true after a successful exact lookup.
- `relationship`: the serialized pair relation, including pending/closed or
  system-locked relations, or null when no relation exists.

Clients must validate peer identity and direction before presenting request
actions, and respect `is_system_locked`. A resolved null is distinct from an
older server omitting these fields. History lists remain capped at 200 per
direction; they must not override the exact lookup. Reads perform no mutation.

## Production Verification (2026-10-31)

After explicit user approval, production source was checked against commit
`4458411`. The server's existing minute-based auto-pull script had already
fetched this commit before the manual verification; a prior statement that it
was not deployed did not account for that automation.

Production `manage.py check` reported no issues. The confirmed Sermo Gunicorn
master received HUP, and all four replacement workers started at 08:48:04
server time. The public API status route returned the expected missing-auth
response (`AUTH@FORMAT`, HTTP 400). This verifies process/route health, not an
authenticated end-to-end relationship response. No manual migration, account
write or credential change was performed. Existing auto-update logs reported
no migrations to apply and unrelated model/migration drift in Chat/User;
that drift was not changed as part of this patch.

Native profile integration and focused review remain follow-up work; older
servers must remain supported. Future deployment gating must account for the
existing automatic pull/migration script, not assume a push is code-only.
