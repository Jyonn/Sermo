# Number Duel Settlement Receipt Compatibility

## Contract

Commit `150ac1f` corrects only the serialized `returned` value. For a funded,
finished match with a reward ledger entry, it is the entry stake plus canonical
net reward points. Practice and unjoined rooms return zero; paid cancellation
returns the actual stake refund. This does not alter settlement, balances,
eligibility, reward records or transaction behavior.

## Approved Production Patch: 2026-11-07

The owner explicitly approved deployment of this compatibility patch only, after
production comparison. Local current-source verification at `9dd7250` passed all
35 `Activity.tests_number_duel` tests and `manage.py check` using secret-free
in-memory SQLite settings. This is not MySQL concurrency or live-duel acceptance.

The production module matched the patch's exact parent file before replacement:

- Before SHA256: `70ff380a793035c86df255e5e2ef60d9c31f16a34ec0f1fe259e96f4d31cc412`.
- Published SHA256: `3e4909b3cfc4e37a216b2276edcb9ad18f5a555cd23351571723b57cd22131a4`.
- The tested local module and exported `150ac1f` artifact had the same published hash.

Only `Activity/number_duel.py` was published, with before/staged fingerprints
checked again immediately before atomic replacement. The original file is backed
up at `/home/ubuntu/sermo-release-backups/number-duel-receipt-20261107-150ac1f/number_duel.py`.
Production Git HEAD remains `2bde0dc`; this was an exact single-file patch, not a
branch pull or deployment of all later commits. Final production diff is exactly
the comment and return-expression change from `150ac1f` (two insertions, one removal).
Untracked operational files were left untouched.

The existing Gunicorn configuration has `reload = True` without a configured
preload override. All four API worker processes on the existing listener rotated
after publication; its master/listener remained available. No unrelated process
was signaled. Production system checks passed before and after publication.
The existing database-access-during-initialization warning remains unresolved.

An unauthenticated HTTPS GET to `/activities/number-duel-2026/duel` returned HTTP
400 with identifier `AUTH@FORMAT` and missing-authorization details. The initial
probe expected 401; the observed 400 is recorded rather than relabeled. It proves
application reachability and rejection, not authenticated settlement behavior.

No migration, database write command, real match, point mutation, provider or
push action was run. Native duel routing remains gated in iOS; this publication
does not enable it or complete its visual/device acceptance. No frontend or
mirror release was performed.
