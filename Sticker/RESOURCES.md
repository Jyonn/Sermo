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
Platform artwork is confined to `frienden/assets/{collection}/{version}/{sha256}.{ext}`.
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

After publication, run `npm run assets:sync` in the frontend. It downloads this release's
Haibara files via the public registry, verifies SHA-256, and updates the small checked-in
mirror cache. Existing hashed files are preserved. Commit the manifest and optimized
files, then publish all four frontend sites. Runtime collection changes refresh in the
background; unknown/new hashes use the canonical asset endpoint and local device cache.
