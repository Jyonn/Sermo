# Social media previews

Douyin and RedNote use JustOneAPI exclusively. Configure `JUSTONEAPI_TOKEN` in a protected server environment file. Never expose it to Vite, client DTOs, logs, or repository files. Requests use the official SDK's HTTP contracts: share-url-transfer/v1, Douyin get-video-detail/v2, RedNote get-note-detail/v1. No HTML/feed scraping fallback remains.

An account with permanent VIP has 7 shared Douyin/RedNote parsing attempts per China-time calendar week (Monday 00:00). An attempt covers short-link expansion plus detail retrieval. Failed upstream attempts count, but missing configuration and permission denials do not. Valid shared caches cost nothing and can be read by non-VIP accounts. A requester ID must be propagated from an authenticated preview endpoint to the worker. Message creation/serialization and statement publication only look up caches, without assigning costs to the sender. Usage is durable; a locked user row serializes quota updates across both platforms and workers.

Successful media caches last at most 15 minutes, shortened by known signed-URL expiry with a one-minute margin. Legacy provider entries expire immediately. Failures are cached for 10 minutes to avoid polling charges. Platform locks coalesce concurrent refreshes for the same cached record. Expired media is omitted from responses until refreshed. A media error may force one refresh, still subject to the current requester's quota. URLs with unknown signature formats use the conservative TTL, never an indefinite cache.

The RedNote v1 response tested on 2026-11-18 exposes some video notes without a video stream. These remain unavailable rather than being falsely classified as galleries or using native audio as video. Live Photo still images are usable; live motion playback is not added by this change. RedNote CDN hosts are allowed explicitly; arbitrary upstream URLs are rejected.

Apply Message migration 0041 before activating the new backend. Keep existing release directories and service environments. Rollback may retain the additive usage table. Frontend release notes are published only after each static origin is verified.
