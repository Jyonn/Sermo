import json
from urllib.parse import urlparse

from django.db import migrations, models


def copy_message_metadata(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    ImageMetadata = apps.get_model('Message', 'ImageMetadata')
    VideoMetadata = apps.get_model('Message', 'VideoMetadata')
    MediaMetadata = apps.get_model('Message', 'MediaMetadata')

    def source_for(message):
        try:
            source_uri = str((json.loads(message.content) or {}).get('uri') or '').strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            source_uri = ''
        source_key = urlparse(source_uri).path.lstrip('/') or f'legacy/message/{message.id}'
        return source_key[:255], source_uri[:500]

    common_fields = (
        'status', 'make', 'model', 'lens_model', 'software', 'taken_at', 'file_size',
        'pixel_width', 'pixel_height', 'latitude', 'longitude', 'address',
        'geocoding_provider', 'geocoding_status', 'geocoding_error', 'error',
    )
    video_fields = ('duration_seconds', 'frame_rate', 'bit_rate', 'video_codec', 'audio_codec')
    for legacy, kind, raw_field, extra_fields in (
        (ImageMetadata, 0, 'raw_exif', ()),
        (VideoMetadata, 1, 'raw_avinfo', video_fields),
    ):
        for row in legacy.objects.select_related('message').iterator():
            source_key, source_uri = source_for(row.message)
            defaults = {field: getattr(row, field) for field in (*common_fields, *extra_fields)}
            defaults.update(
                source_uri=source_uri,
                kind=kind,
                raw_metadata=getattr(row, raw_field) or {},
            )
            MediaMetadata.objects.update_or_create(source_key=source_key, defaults=defaults)


def restore_message_metadata(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    ImageMetadata = apps.get_model('Message', 'ImageMetadata')
    VideoMetadata = apps.get_model('Message', 'VideoMetadata')
    MediaMetadata = apps.get_model('Message', 'MediaMetadata')

    common_fields = (
        'status', 'make', 'model', 'lens_model', 'software', 'taken_at', 'file_size',
        'pixel_width', 'pixel_height', 'latitude', 'longitude', 'address',
        'geocoding_provider', 'geocoding_status', 'geocoding_error', 'error',
    )
    video_fields = ('duration_seconds', 'frame_rate', 'bit_rate', 'video_codec', 'audio_codec')
    for message in Message.objects.iterator():
        try:
            source_uri = str((json.loads(message.content) or {}).get('uri') or '').strip()
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        source_key = urlparse(source_uri).path.lstrip('/')
        if not source_key:
            continue
        metadata = MediaMetadata.objects.filter(source_key=source_key).first()
        if metadata is None:
            continue
        if metadata.kind == 0:
            defaults = {field: getattr(metadata, field) for field in common_fields}
            defaults['raw_exif'] = metadata.raw_metadata or {}
            ImageMetadata.objects.update_or_create(message_id=message.id, defaults=defaults)
        elif metadata.kind == 1:
            defaults = {field: getattr(metadata, field) for field in (*common_fields, *video_fields)}
            defaults['raw_avinfo'] = metadata.raw_metadata or {}
            VideoMetadata.objects.update_or_create(message_id=message.id, defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [('Message', '0015_message_sync_events')]

    operations = [
        migrations.CreateModel(
            name='MediaMetadata',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_key', models.CharField(max_length=255, unique=True)),
                ('source_uri', models.CharField(max_length=500)),
                ('kind', models.IntegerField(db_index=True)),
                ('status', models.IntegerField(db_index=True, default=0)),
                ('raw_metadata', models.JSONField(blank=True, default=dict)),
                ('duration_seconds', models.FloatField(blank=True, null=True)),
                ('file_size', models.BigIntegerField(blank=True, null=True)),
                ('pixel_width', models.PositiveIntegerField(blank=True, null=True)),
                ('pixel_height', models.PositiveIntegerField(blank=True, null=True)),
                ('frame_rate', models.FloatField(blank=True, null=True)),
                ('bit_rate', models.BigIntegerField(blank=True, null=True)),
                ('video_codec', models.CharField(blank=True, default='', max_length=64)),
                ('audio_codec', models.CharField(blank=True, default='', max_length=64)),
                ('make', models.CharField(blank=True, default='', max_length=255)),
                ('model', models.CharField(blank=True, default='', max_length=255)),
                ('lens_model', models.CharField(blank=True, default='', max_length=255)),
                ('software', models.CharField(blank=True, default='', max_length=255)),
                ('taken_at', models.DateTimeField(blank=True, null=True)),
                ('latitude', models.FloatField(blank=True, null=True)),
                ('longitude', models.FloatField(blank=True, null=True)),
                ('address', models.CharField(blank=True, default='', max_length=500)),
                ('geocoding_provider', models.CharField(blank=True, default='', max_length=32)),
                ('geocoding_status', models.IntegerField(db_index=True, default=0)),
                ('geocoding_error', models.CharField(blank=True, default='', max_length=500)),
                ('error', models.CharField(blank=True, default='', max_length=500)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'default_manager_name': 'objects'},
        ),
        migrations.RunPython(copy_message_metadata, restore_message_metadata),
    ]
