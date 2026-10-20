# Make an Entrance / 主角登场

## Lifecycle

- Campaign key: `main-character-bgm-2026`; mode `bgm_collection`, theme `bgm`.
- Registration makes the campaign available to space administrators. It never claims it for any space.
- The existing authenticated space activity claim endpoint starts exactly 30 days for that space. Claims are idempotent and never extend the end date.
- Verified members opt in by choosing blue or pink. First track is a gift. Pre-join interactions are not backfilled.
- Styles share one collection. Six distinct owned tracks can be equipped. Earned tracks and entrance settings remain available after the event; earning and new enrollment stop at expiry.

## Server-owned tasks

Beijing dates, not rolling 24-hour windows. Daily maximum 20 inspiration:

| Task | Limit | Inspiration each |
| --- | --- | --- |
| Publish a public post | 1 | 6 |
| Comment on different public posts by others | 3 | 3 |
| Like posts by different authors | 5 | 1 |

An active day requires one qualifying post or comments on two different posts by others. Likes alone do not qualify. Anonymous/friends-only/self-interactions do not qualify. Comments must contain text. Each like target and comment target earns only once per day; repeated requests and like/unlike cycles never award twice. Ledger rows survive content deletion. Counters cannot be supplied by clients.

| Track | Active days | Total inspiration |
| --- | --- | --- |
| 1 | 0 | 0 |
| 2 | 1 | 10 |
| 3 | 2 | 25 |
| 4 | 3 | 40 |
| 5 | 4 | 60 |
| 6 | 5 | 80 |
| 7 | 7 | 105 |
| 8 | 9 | 130 |
| 9 | 11 | 155 |
| 10 | 12 | 180 |
| 11 | 14 | 210 |
| 12 | 15 | 240 |

Both gates must be met. No streak reset, random reward draws or paid shortcuts.

## API and compatibility

- `GET /activities/active`, `GET /activities/<key>` include `bgm` state; locked tracks have no playback URI.
- `POST /activities/<key>/bgm`: `style`, optional `slots`, `mode` (`off`, `auto`, `manual`), `track` (owned key or `random`), `receive_audio` boolean. All require normal user auth; only verified users can join.
- `GET /activities/bgm/me`: owned collection or null, scoped to authenticated account/space. Used by chat and the permanent personalization entry.
- `POST /activities/<key>/bgm/enter`: `chat_id`, `request_id`, and `track` for manual mode. The server verifies membership, normal-chat purpose, mute restrictions, equipped/owned track, and mode. It atomically creates one system message and one existing type-5 audio message. No activity or growth points are awarded for entrances.
- Same-chat cooldown is 10 minutes; account-wide cooldown is 30 seconds. Reused request IDs do not create messages twice.
- Old clients display normal system/audio messages. AudioTranscript is prefilled with song title and confirmed author only; no fabricated author or speech recognition.
- New clients present audio as a compact playback pill and attempt playback only for live foreground arrivals. History loading and the initial snapshot do not autoplay. Receiving sounds is opt-in. Browser autoplay rejection always leaves a playback control.
- Audio clips are public event media, not DRM-protected rewards. Unlocking controls collection ownership/equipping, not secrecy of audio bytes. No other storage files become public.

## Deployment

1. Run focused tests and Django checks. Apply additive Activity migrations 0013/0014 before enabling the campaign.
2. Publish frontend `/activity/bgm/catalog.json` and all 12 MP3s to the four sites. Never overwrite a claimed campaign's catalog.
3. On the backend server, register the verified manifest and files:

```sh
python manage.py configure_bgm_activity \
  --directory /home/ubuntu/sermo-frontend/dist/activity/bgm \
  --origin https://sermo.6-79.cn --enable
```

4. Confirm 12 ready MediaAssets, ready AudioTranscripts, a manual 30-day enabled campaign and **zero auto-created SpaceActivity rows**.
5. Each space administrator may now claim it in the existing activity-management Drawer. Do not start a space's clock as part of deployment.

The manifest SHA-256 hashes are checked before registration. Authors may be blank; UI omits them. No credentials or user data are included in the manifest.
