# Platform resources

Super administrators manage collections in the platform console's Resources section.
The authenticated API is `/platform-admin/resources`, `/resources/upload`, and
`/resources/{id}` under the same platform-admin prefix. These APIs use the existing
platform administrator authentication; there is no separate agent credential.

Only published collections and enabled entries are listed by
`GET /resources/collections/{slug}`. Managed expression assets are excluded from the
ordinary global sticker discovery feed and have their own collection tab.
Unpublishing never deletes bucket objects, registered sticker IDs, or old messages.
Resource files are public platform artwork, not a private-document storage service.

## Storage

New user uploads use `frienden/avatar`, `frienden/messages`, `frienden/chat-background`,
and `frienden/space-identity`; existing `sermo/` references remain supported.
New platform artwork is grouped below `frienden/assets/emojis/{collection}`,
`avatar-frames/{collection}`, `backgrounds/{collection}`, `illustrations/{collection}`,
and `activities/{activity-name}`. Existing flat resource URLs/IDs remain valid.
No listing, moving, or deletion of other applications' bucket objects is performed.
Image files are decoded and verified, stripped of metadata, and re-encoded into an
original PNG plus display/thumbnail WebP variants. Uploads are insert-only; existing
objects must match their expected content hash. Storage credentials remain in server
Config and are never sent to frontend bundles or management responses.

## Repeatable imports for operators and agents

Create a manifest beside the original image files:

```json
{"slug":"haibara-v2","title":"小哀","title_en":"Ai Haibara","version":"v2","category":"emoji",
 "assets":[{"slug":"joy","title":"开心","title_en":"Joy","file":"01.png"}]}
```

Run on the server with its existing Config:

```sh
python manage.py publish_resources /path/to/publish.json --publish
```

Imports are repeatable. A slug cannot silently replace different artwork: use a new
collection/version for a replacement set. Each import and admin mutation is audited.
Original images are deployment input and do not need to be committed to the app repo.

After publication, run `npm run assets:sync` in the frontend. It verifies this release's
Haibara files via the public registry and writes URL metadata only. Artwork no longer
ships in new frontend distributions. Publish all four sites; browser caches reuse the
stable resource API URLs. Keep old deployed files for stale clients.

## Activity migration

`python manage.py import_activity_resources manifest.json --output asset-map.json --update-references`
imports trusted repository PNG/WebP/SVG/audio/catalog files byte-for-byte. The manifest
contains local `file`, public legacy `alias`, and `activity` fields. Uploads are confined
to the activity namespace. It updates activity configuration and BGM MediaAsset URIs
without changing IDs, claims, rewards, transcripts, or existing bucket objects.
Verify hashes before removing local source files. Old deployed files must remain for
stale clients; new distributions resolve files through the stable resource API.

## Personal sticker series

Authenticated `/stickers/series` lists/creates a user's own series; `/series/{id}`
renames/removes it. Removing a series moves its stickers to Unsorted, never deletes
the underlying artwork. `/series/assign` atomically moves up to 200 owned stickers.
Names have a combined limit of 10 ASCII units (non-ASCII characters count as two).
`GET /stickers/?series_id=0` selects Unsorted; a positive ID selects a series before
pagination. All ownership checks are enforced by the server.

Series now represent moderated public collections, separate from the user's home.
Collection uploads record `is_uploaded` provenance and default to `in_home=False`.
Historical provenance follows the existing earliest-source attribution and excludes
platform artwork. Later collected copies are not upload sources. Published and pending
snapshots are separate; drafts remain private, submissions are frozen, and publication,
updates and deletion require platform review. Submission sends the configured admin an
email and creates a durable queue entry. `/platform-admin/sticker-packs` reviews the
queue. `/stickers/packs` lists approved snapshots; collecting is an explicit operation.
