import datetime

from django.db import migrations


BEIJING_OFFSET = datetime.timedelta(hours=8)


def fix_existing_image_capture_times(apps, schema_editor):
    MediaAsset = apps.get_model('Message', 'MediaAsset')
    image_kind = 0
    queryset = MediaAsset.objects.filter(kind=image_kind, taken_at__isnull=False)
    for asset in queryset.iterator(chunk_size=500):
        # Image EXIF timestamps have no timezone. Older imports interpreted the
        # camera's Beijing wall time as UTC, so restore the original instant.
        asset.taken_at -= BEIJING_OFFSET
        asset.save(update_fields=['taken_at'])


class Migration(migrations.Migration):
    dependencies = [('Message', '0025_message_appearance')]

    operations = [migrations.RunPython(fix_existing_image_capture_times, migrations.RunPython.noop)]
