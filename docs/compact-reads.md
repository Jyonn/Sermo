# Compact Read Transports

`GET /messages/?chat_id=...&limit=31&format=compact-v1` and
`GET /users/me/growth?format=compact-v1` opt into a lossless transport.
Growth acknowledgement POST may also opt in with the same query parameter.
Old clients retain the original bodies when no format is supplied.

The API envelope, authentication, membership, permissions, pagination and
growth policy are unchanged. There is no cross-request or cross-user cache.
Messages retain authors, mentions, replies, media and system-message payloads.
Growth retains all levels, rewards, capabilities and recent events.

Compact bodies have `format`, `kind` (`messages` or `growth`), `schemas`,
`records`, and `root`. Schemas store sorted field names once. Each record is
`[schemaIndex, ...encodedValues]`. An encoded array is `[-1, ...encodedItems]`;
an object reference is `[-2, recordIndex]`. Scalars are unchanged. Equal whole
objects are deduplicated only when their full serialized content matches;
same-ID objects with different viewer-specific fields remain distinct.
Clients decode references into independent objects, not shared mutable state.

Each request compares real JSON sizes and sends the legacy body if the compact
body would be larger. For gzip clients, compare gzipped sizes instead. Scoped
Django gzip middleware covers chat lists, message reads and growth reads, with
`Vary: Accept-Encoding` and Django's standard compression safeguards. This is
transport optimization, not deletion of business fields or HTTP cache reuse.

Tests: `manage.py test Message.test_compact Activity.tests_bgm Chat.test_compact`
with isolated test settings; frontend `tests/compactTransport.test.ts` tests
decoding, independent objects, invalid schemas/references and API negotiation.
