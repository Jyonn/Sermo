# Collaboration Invitation Decline

Local implementation; not approved for production deployment yet.

`DELETE /chats/submissions/invite?invite_id=<id>` uses the ordinary member's
Bearer session. It returns the canonical invitation object, not a chat or member.
Only the intended recipient in the invitation's space may decline a pending,
unexpired invitation that has not already resulted in membership. The existing
business-error envelope applies; clients must inspect `identifier`, not only HTTP
status. Sender, unrelated accounts and cross-space accounts cannot decline.

Both submission and Research invitation payloads add `can_decline`. Clients must
treat an absent flag as false for compatibility with older deployments. Declined
invitations report `status: "declined"`, `can_accept: false` and
`can_decline: false`. Repeating a decline as the same recipient succeeds without
another state change. Accepted/expired/conflicting invitations cannot be declined.
Decline never joins, removes or changes a member; a later fresh invitation remains
possible. Existing GET and POST acceptance contracts are unchanged.

The transaction follows acceptance's lock order: Research project when relevant,
chat, then invitation. The eligibility function is shared by reporting and
enforcement. Research revision increments once to invalidate pending-invite
snapshots. Both parties receive existing chat-state invalidation signals. This is
not a guarantee of live client delivery or a SQLite concurrency certification.

Migration `0027_submission_invite_declined` adds the status choice `3`; no records
are rewritten or deleted. Deployment must include migration-state synchronization.
Existing unrelated migration drift (Chat blocked-word index names and User OAuth
model metadata) was observed by the all-app dry-run and is not changed here.

Native UI integration and deployment approval remain separate steps. Do not expose
a local-success decline button against an older backend, and do not auto-replay a
decline after an ambiguous response; fetch the invitation to reconcile first.

## Local Verification

Using secret-free `Sermo.isolated_test_settings` and in-memory SQLite: seven new
decline tests and the related Chat/submission/Research suites pass (107 tests).
`manage.py check` passes. `sqlmigrate Chat 0027` reports a no-op field alteration.
The full Chat run has one unrelated retired-blocked-word fixture error; the same
error reproduces on an untouched HEAD archive. The migration dry-run's unrelated
index/OAuth differences also reproduce there. Neither baseline issue is changed.
Initial new-test failures were fixture verification-tier setup and incorrect HTTP
status assumptions; final tests assert exact business identifiers. SQLite passing
does not establish MySQL lock concurrency or production deployment acceptance.
