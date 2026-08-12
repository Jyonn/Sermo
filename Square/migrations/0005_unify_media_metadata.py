import datetime

from django.db import migrations, models
import django.db.models.deletion


def copy_square_metadata(apps, schema_editor):
    Config = apps.get_model('Config', 'Config')
    StatementMedia = apps.get_model('Square', 'StatementMedia')
    MediaMetadata = apps.get_model('Message', 'MediaMetadata')
    domain_row = Config.objects.filter(key='QINIU_DOMAIN').first()
    domain = str(domain_row.value if domain_row else '').strip().replace('https://', '').replace('http://', '').strip('/')
    for media in StatementMedia.objects.exclude(kind=1).iterator():
        payload = dict(media.metadata or {})
        taken_at = payload.get('taken_at')
        if isinstance(taken_at, (int, float)):
            payload['taken_at'] = datetime.datetime.fromtimestamp(taken_at, tz=datetime.timezone.utc)
        allowed = {
            field.name for field in MediaMetadata._meta.fields
            if field.name not in {'id', 'source_key', 'source_uri', 'kind', 'raw_metadata', 'updated_at'}
        }
        defaults = {key: value for key, value in payload.items() if key in allowed}
        defaults.update(
            status=media.metadata_status,
            error=media.metadata_error,
            raw_metadata={},
        )
        metadata, created = MediaMetadata.objects.get_or_create(
            source_key=media.key,
            defaults={
                **defaults,
                'source_uri': f'https://{domain}/{media.key}' if domain else media.key,
                'kind': 0 if media.kind == 0 else 1,
            },
        )
        if not created:
            for key, value in defaults.items():
                if value not in (None, '', {}):
                    setattr(metadata, key, value)
            metadata.save()
        media.media_metadata_id = metadata.id
        media.save(update_fields=['media_metadata'])


def restore_square_metadata(apps, schema_editor):
    StatementMedia = apps.get_model('Square', 'StatementMedia')
    for media in StatementMedia.objects.exclude(media_metadata_id=None).select_related('media_metadata').iterator():
        metadata = media.media_metadata
        payload = {}
        for field in (
            'duration_seconds', 'file_size', 'pixel_width', 'pixel_height', 'frame_rate', 'bit_rate',
            'video_codec', 'audio_codec', 'make', 'model', 'lens_model', 'software', 'taken_at',
            'latitude', 'longitude', 'address', 'geocoding_provider', 'geocoding_status',
        ):
            value = getattr(metadata, field)
            payload[field] = value.timestamp() if hasattr(value, 'timestamp') else value
        media.metadata_status = metadata.status
        media.metadata = payload
        media.metadata_error = metadata.error
        media.save(update_fields=['metadata_status', 'metadata', 'metadata_error'])


class Migration(migrations.Migration):
    dependencies = [
        ('Config', '0001_initial'),
        ('Message', '0016_mediametadata'),
        ('Square', '0004_statementcomment_parent'),
    ]

    operations = [
        migrations.AddField(
            model_name='statementmedia',
            name='media_metadata',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='statement_media_items',
                to='Message.mediametadata',
            ),
        ),
        migrations.RunPython(copy_square_metadata, restore_square_metadata),
        migrations.RemoveField(model_name='statementmedia', name='metadata'),
        migrations.RemoveField(model_name='statementmedia', name='metadata_error'),
        migrations.RemoveField(model_name='statementmedia', name='metadata_status'),
    ]
