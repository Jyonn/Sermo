import uuid

from django.db import migrations, models


MEDIA_TYPES = (1, 4, 5)


def backfill_message_blob_slugs(apps, schema_editor):
    Message = apps.get_model('Message', 'Message')
    existing = set(
        Message.objects.exclude(blob_slug__isnull=True)
        .exclude(blob_slug='')
        .values_list('blob_slug', flat=True)
    )

    for message in Message.objects.filter(type__in=MEDIA_TYPES).filter(models.Q(blob_slug__isnull=True) | models.Q(blob_slug='')).iterator():
        blob_slug = uuid.uuid4().hex
        while blob_slug in existing:
            blob_slug = uuid.uuid4().hex
        existing.add(blob_slug)
        message.blob_slug = blob_slug
        message.save(update_fields=['blob_slug'])


class Migration(migrations.Migration):

    dependencies = [
        ('Message', '0002_alter_message_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='blob_slug',
            field=models.CharField(blank=True, db_index=True, max_length=32, null=True, unique=True),
        ),
        migrations.RunPython(backfill_message_blob_slugs, migrations.RunPython.noop),
    ]
