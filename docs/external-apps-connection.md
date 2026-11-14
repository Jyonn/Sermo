# External app connections

Each external service owns its account and maps the verified canonical Frienden
phone to a unique account across spaces. Frienden OAuth subjects and the user's
explicit permission to access an external account remain scoped to their space.
Existing external accounts with a disputed phone association need ownership review.

Pocklo is registered in the Frienden OAuth registry as `pocklo`. Frienden's
separate confidential Pocklo client uses `files.read shares.create` and the exact callback
`https://api.sermo.jyonn.space/users/pocklo/callback`. The Pocklo administrator
must enable the client for the phone proof endpoint and approve sharing. Set `client_id`,
`client_secret`, and `storage_key` (Fernet) in the Frienden
service user's mode-0600 `~/.config/frienden/pocklo.json`. Preserve the encryption
key across deployments. Apply User migration `0096_pocklo_connection` before
reloading the API. Previously linked accounts with read-only grants must approve
the new scope. Frienden sends a public share link and resolves its metadata for
the chat card. Download redirects validate the share again and accept only the
known Pocklo CDN. No file bytes pass through Frienden. Disconnecting a space
removes its local link. Pocklo's settings control the shared grant and previously
created share links separately.

Chronelle uses its existing Frienden application and callback. Run
`python manage.py enable_chronelle_phone` to check the registry and
`python manage.py enable_chronelle_phone --apply` to approve the additional
phone scope through the audited registry. Existing profile-only grants retain
their original consent.

The StillFolio picker returns one existing public share link after explicit
selection. The Frienden caller verifies the exact popup origin and window,
state and share URL, then inserts it in the current conversation draft.
