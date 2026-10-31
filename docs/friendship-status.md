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

This protocol addition needs a separate deployment before production clients
can rely on it. Native profile integration and focused review remain follow-up
work; older servers must remain supported.
