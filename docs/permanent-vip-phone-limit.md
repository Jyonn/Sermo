# Permanent VIP phone quota

Across all spaces, an account below effective LV9 cannot claim permanent VIP
while another permanent VIP account with the same phone is below effective LV9.
LV9+ candidates are exempt. Existing claims remain idempotent and are not revoked.
Verified phone, verified email and the campaign capacity still apply. There is no
minimum claim level: an eligible LV1 account can claim. Recognized Chinese phone
representations share the quota. The old slot-dependent LV4–6 tiers are removed;
legacy API fields report `required_level=1`, `level=true`, and no level tiers.

`requirements_for` is shared by status reporting and claim enforcement. Claims
serialize through the existing campaign row lock before rechecking the quota;
an earlier eligibility response cannot reserve a second claim.

## Approved deployment — 2026-11-08

The owner authorized deployment of commit `6006019`. Production HEAD remains
`2bde0dc`; only this commit's User model/validator/test and Chinese translation
changes were applied. The existing independent Number Duel receipt patch and
untracked operational files were preserved. No migration or account mutation
command was run. Original files are backed up under
`/home/ubuntu/sermo-release-backups/vip-phone-20261108-6006019/`.

The server has no gettext compiler. Its updated PO file was compiled locally and
the resulting MO synchronized back. Production system checks and all nine
`User.tests_vip_phone_limit` tests passed using isolated in-memory SQLite settings.
The four Gunicorn workers rotated under the existing reload configuration;
the master and listener remained available. These tests do not independently
measure MySQL concurrency. The pre-existing database-access-at-initialization
warning remains.

Frontend release `2026.11.08.3` displays the quota rule and the reason when blocked.
