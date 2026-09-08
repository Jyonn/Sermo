# QZone migration runbook

Run the import only after the platform administrator grants the target space the
`qq_identity_binding` feature. The space administrator switch controls member
binding, but it does not need to be enabled for the historical import itself.

Use the staged flow in production so each checkpoint can be inspected before
continuing:

```bash
python manage.py import_qzone_migration \
  --space SPACE_SLUG \
  --input /absolute/path/to/qzone-migration.json \
  --stage preflight

python manage.py import_qzone_migration --space SPACE_SLUG --input /absolute/path/to/qzone-migration.json --stage source
python manage.py import_qzone_migration --space SPACE_SLUG --input /absolute/path/to/qzone-migration.json --stage identity
python manage.py import_qzone_migration --space SPACE_SLUG --input /absolute/path/to/qzone-migration.json --stage media
python manage.py import_qzone_migration --space SPACE_SLUG --input /absolute/path/to/qzone-migration.json --stage media --upload-media
python manage.py import_qzone_migration --space SPACE_SLUG --input /absolute/path/to/qzone-migration.json --stage projection
python manage.py import_qzone_migration --space SPACE_SLUG --input /absolute/path/to/qzone-migration.json --stage verify
```

All stages are resumable. Source rows, identities, media manifests, uploaded
assets, Statements, and comments are reused when the command is run again.
Use `--retry-failed` with the media upload stage after fixing missing files or
storage configuration. `--limit N` limits source imports to the first `N` posts
while retaining every dependency and comment for those posts.

After an interrupted projection, skip rows that are already linked and continue
from the remaining posts and comments:

```bash
python manage.py import_qzone_migration \
  --space SPACE_SLUG \
  --input /absolute/path/to/qzone-migration.json \
  --stage projection \
  --skip-projected
```

Projection reads source rows in primary-key batches, so its peak query memory is
bounded by `--batch-size`. It reports separate progress for posts and comments.

The media upload stage requires the existing Qiniu access key, secret key,
bucket, and public resource domain configuration. It hashes local files first
and reuses an existing `MediaAsset` with the same content hash and size.
