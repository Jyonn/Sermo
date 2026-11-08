# External app connections

Each external service owns its account and maps the verified canonical Frienden
phone to a unique account across spaces. Frienden OAuth subjects and the user's
explicit permission to access an external account remain scoped to their space.
Existing external accounts with a disputed phone association need ownership review.

Pocklo is registered in the Frienden OAuth registry as `pocklo`. Frienden's
separate confidential Pocklo client uses `files.read` and the exact callback
`https://api.sermo.jyonn.space/users/pocklo/callback`. The Pocklo administrator
must enable the client for the phone proof endpoint. Set `client_id`,
`client_secret`, `storage_key` (Fernet) and `download_origin` in the Frienden
service user's mode-0600 `~/.config/frienden/pocklo.json`. Preserve the encryption
key across deployments. Apply User migration `0096_pocklo_connection` before
reloading the API. File copies are limited to 20 MiB and use an exact HTTPS CDN
origin. Disconnecting a space removes its local link. Pocklo's authorization
settings control the grant shared by linked spaces.

Chronelle uses its existing Frienden application and callback. Run
`python manage.py enable_chronelle_phone` to check the registry and
`python manage.py enable_chronelle_phone --apply` to approve the additional
phone scope through the audited registry. Existing profile-only grants retain
their original consent.

The StillFolio picker returns one existing public share link after explicit
selection. The Frienden caller verifies the exact popup origin and window,
state and share URL, then inserts it in the current conversation draft.
