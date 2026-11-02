# Independent Research Conversation Read

Development branch only. Deployment requires separate user confirmation. No
migration, production command, APNs enablement or default-branch update is included.

`GET /research/<project_id>/chat/` returns the existing canonical chat-list payload
for exactly one research project. It requires authentication, the current space's
Research feature and active membership of an undeleted project chat. It rejects
normal/submission/direct rooms, foreign spaces, query parameters and oversized IDs.
It does not require the unrelated normal Chat feature. Archived projects remain
readable while membership is active.

The endpoint reuses `project_for`, `has_active_member` and
`ChatListView.build_chat_payload`; no alternate message visibility or unread policy
is introduced. Hidden/recalled messages are excluded by the shared preview policy.
Reading does not acknowledge messages or create a ChatReadState. All responses,
including authentication and unsupported-method failures, are private/no-store.

The iOS client preserves its existing list path when ordinary Chat is available.
When Chat is disabled it uses this endpoint and fails closed if the server has not
deployed it; it does not retry against the gated normal list or synthesize a chat.
Client checks validate the canonical group, purpose, project chat ID, membership and
session generation before presenting it. Production rollout must verify this route
and the existing message read/send/reveal contracts together in a Research-only
space before claiming end-to-end parity.

Local isolated settings: six endpoint tests and 38 related research/notification
tests passed. Django check reports no issues apart from the existing initialization
database-access warning. No production database or delivery credential was used.

Combined account/native-push/research regression: 165 passed
(`/private/tmp/frienden-272-backend-full.log`). The first expanded test attempt used
an incorrect hidden-state fixture field; corrected it to the actual row-presence
policy, without changing the shared message implementation. No migration needed.
