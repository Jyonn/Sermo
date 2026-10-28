# Research consent and global calendar privacy

- Research invitations reuse private SubmissionInvite message cards and the existing authenticated invitation endpoint. Research payloads carry a project reference; no membership is created before acceptance.
- Acceptance locks the project, chat and invitation consistently. It rechecks the space gate, recipient, current owner, project state, expiry and join policy. Transferring ownership expires outstanding invitations.
- Migration 0023 adds a per-user ResearchCalendarPreference. A read never enables sharing. Before confirmation, the settings form proposes busy/free for all groups; effective access remains hidden unless an earlier explicit project policy exists.
- Confirmed defaults and group overrides apply across research projects. New groups follow the selected default. Detail access requires an explicit per-group choice. Preferences are tied to the bound calendar subject; rebinding another identity fails closed.
- Saving global permissions revokes this user's outstanding display sessions in every active project before updating the preference. Remote revocation failure prevents the change from being reported as saved.
- Existing per-project policies remain a compatibility fallback only until the user confirms a global policy. Legacy requests without the new global-default field never become global grants; after global confirmation, legacy policy writes require a client update. No migration backfills or enables permissions.

Validation: isolated SQLite tests cover invitation consent, owner changes, foreign groups, overlap, global defaults, rebinding and cross-project revocation. Production deployment requires the additive migration before frontend release.
