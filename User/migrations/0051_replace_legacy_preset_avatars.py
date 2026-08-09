import re

from django.db import migrations


LEGACY_PRESET_PATTERN = re.compile(r'/([0-9]{2})\.svg(?:\?.*)?$')
NEW_PRESET_BASE_URI = 'https://sermo.jyonn.space/assets/avatars/v2'
NEW_PRESET_COUNT = 36


def replace_legacy_preset_avatars(apps, schema_editor):
    User = apps.get_model('User', 'User')
    updates = []

    for user in User.objects.filter(avatar_type='preset').only('id', 'avatar_uri').iterator(chunk_size=500):
        match = LEGACY_PRESET_PATTERN.search(user.avatar_uri or '')
        if not match:
            continue
        legacy_id = int(match.group(1))
        preset_id = ((legacy_id - 1) % NEW_PRESET_COUNT) + 1
        user.avatar_uri = f'{NEW_PRESET_BASE_URI}/{preset_id:02d}.png'
        updates.append(user)
        if len(updates) >= 500:
            User.objects.bulk_update(updates, ['avatar_uri'], batch_size=500)
            updates.clear()

    if updates:
        User.objects.bulk_update(updates, ['avatar_uri'], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0050_notificationevent_statement_removed'),
    ]

    operations = [
        migrations.RunPython(replace_legacy_preset_avatars, migrations.RunPython.noop),
    ]
