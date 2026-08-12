from urllib.parse import urlparse

from django.db import migrations, models
import django.db.models.deletion
from Message import models as message_models


def table_columns(schema_editor, table_name):
    with schema_editor.connection.cursor() as cursor:
        description = schema_editor.connection.introspection.get_table_description(cursor, table_name)
    return {column.name for column in description}


class ResumeAddField(migrations.AddField):
    """Add a field unless a previous non-transactional attempt already did so."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)
        if field.column in table_columns(schema_editor, model._meta.db_table):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)


class ResumeCreateModel(migrations.CreateModel):
    """Create a table unless a previous non-transactional attempt already did so."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.name)
        tables = set(schema_editor.connection.introspection.table_names())
        if model._meta.db_table in tables:
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)


class ResumeRemoveField(migrations.RemoveField):
    """Remove a field unless a previous non-transactional attempt already did so."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        field = model._meta.get_field(self.name)
        if field.column not in table_columns(schema_editor, model._meta.db_table):
            return
        super().database_forwards(app_label, schema_editor, from_state, to_state)


def rename_media_metadata_table(apps, schema_editor):
    """Resume safely when MySQL committed the rename before migration failed."""
    old_table = 'Message_mediametadata'
    new_table = 'Message_mediaasset'
    tables = set(schema_editor.connection.introspection.table_names())

    if old_table in tables and new_table in tables:
        raise RuntimeError(
            f'Both {old_table} and {new_table} exist. Refusing to guess which table '
            'contains the authoritative media data.'
        )
    if new_table in tables:
        return
    if old_table not in tables:
        raise RuntimeError(
            f'Neither {old_table} nor {new_table} exists. The media migration cannot continue.'
        )

    quoted_old = schema_editor.quote_name(old_table)
    quoted_new = schema_editor.quote_name(new_table)
    schema_editor.execute(f'ALTER TABLE {quoted_old} RENAME TO {quoted_new}')


def consolidate_assets(apps, schema_editor):
    Config = apps.get_model('Config', 'Config')
    Message = apps.get_model('Message', 'Message')
    MediaAsset = apps.get_model('Message', 'MediaAsset')
    MediaAssetAlias = apps.get_model('Message', 'MediaAssetAlias')
    StatementMedia = apps.get_model('Square', 'StatementMedia')

    # Reaching RemoveField means consolidation already completed. MySQL may have
    # committed that DDL even though Django did not record the migration.
    if 'blob_slug' not in table_columns(schema_editor, Message._meta.db_table):
        return

    for asset in MediaAsset.objects.filter(blob_slug__isnull=True).iterator():
        asset.blob_slug = message_models.generate_media_blob_slug()
        asset.save(update_fields=['blob_slug'])

    kind_by_message_type = {1: 0, 4: 1, 5: 2, 2: 3}
    for message in Message.objects.filter(type__in=kind_by_message_type).iterator():
        try:
            payload = __import__('json').loads(message.content)
        except (TypeError, ValueError):
            payload = {}
        source_uri = str(payload.get('uri') or '').strip()
        source_key = urlparse(source_uri).path.lstrip('/')
        if not source_key:
            continue
        defaults = dict(
            source_uri=source_uri,
            kind=kind_by_message_type[message.type],
            mime_type=str(payload.get('mime_type') or '')[:100],
            file_name=str(payload.get('file_name') or '')[:180],
            duration_seconds=payload.get('duration_seconds'),
            file_size=payload.get('file_size'),
            status=0 if message.type in {1, 4} else 1,
            geocoding_status=0 if message.type in {1, 4} else 3,
            blob_slug=message_models.generate_media_blob_slug(),
        )
        asset, _ = MediaAsset.objects.get_or_create(source_key=source_key, defaults=defaults)
        message.media_asset_id = asset.id
        message.save(update_fields=['media_asset'])
        if message.blob_slug:
            MediaAssetAlias.objects.get_or_create(slug=message.blob_slug, defaults={'asset_id': asset.id})

    domain_row = Config.objects.filter(key='QINIU_DOMAIN').first()
    domain = str(domain_row.value if domain_row else '').strip().replace('https://', '').replace('http://', '').strip('/')
    for media in StatementMedia.objects.iterator():
        if media.media_metadata_id:
            asset = MediaAsset.objects.get(id=media.media_metadata_id)
        else:
            source_uri = f'https://{domain}/{media.key}' if domain else media.key
            kind = {0: 0, 1: 2, 2: 1}[media.kind]
            asset, _ = MediaAsset.objects.get_or_create(
                source_key=media.key,
                defaults=dict(
                    source_uri=source_uri, kind=kind,
                    mime_type=media.mime_type or '', duration_seconds=media.duration_seconds,
                    status=0 if kind in {0, 1} else 1,
                    geocoding_status=0 if kind in {0, 1} else 3,
                    blob_slug=message_models.generate_media_blob_slug(),
                ),
            )
            media.media_metadata_id = asset.id
            media.save(update_fields=['media_metadata'])
        if media.blob_slug:
            MediaAssetAlias.objects.get_or_create(slug=media.blob_slug, defaults={'asset_id': asset.id})


class Migration(migrations.Migration):
    dependencies = [('Config', '0001_initial'), ('Message', '0017_remove_legacy_metadata_models'), ('Square', '0005_unify_media_metadata')]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunPython(rename_media_metadata_table, migrations.RunPython.noop)],
            state_operations=[migrations.RenameModel(old_name='MediaMetadata', new_name='MediaAsset')],
        ),
        ResumeAddField(model_name='mediaasset', name='blob_slug', field=models.CharField(db_index=True, max_length=32, null=True, unique=True)),
        ResumeAddField(model_name='mediaasset', name='mime_type', field=models.CharField(blank=True, default='', max_length=100)),
        ResumeAddField(model_name='mediaasset', name='file_name', field=models.CharField(blank=True, default='', max_length=180)),
        ResumeAddField(
            model_name='message', name='media_asset',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='messages', to='Message.mediaasset'),
        ),
        ResumeCreateModel(
            name='MediaAssetAlias',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slug', models.CharField(db_index=True, max_length=32, unique=True)),
                ('asset', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='aliases', to='Message.mediaasset')),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.RunPython(consolidate_assets, migrations.RunPython.noop),
        migrations.AlterField(model_name='mediaasset', name='blob_slug', field=models.CharField(db_index=True, default=message_models.generate_media_blob_slug, max_length=32, unique=True)),
        ResumeRemoveField(model_name='message', name='blob_slug'),
        migrations.AlterField(
            model_name='message', name='type',
            field=models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9)]),
        ),
    ]
