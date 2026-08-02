from django.db import migrations


FRAME_REPLACEMENTS = {
    'lucky': 'papercut',
    'crown': 'polaroid',
    'headphones': 'soundwave',
    'cat-ears': 'butterfly',
    'ribbon-bow': 'blossom',
}


def replace_accessory_avatar_frames(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    for retired, replacement in FRAME_REPLACEMENTS.items():
        user_model.objects.filter(avatar_frame_style=retired).update(
            avatar_frame_style=replacement,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0038_retire_low_identity_avatar_frames'),
    ]

    operations = [
        migrations.RunPython(
            replace_accessory_avatar_frames,
            migrations.RunPython.noop,
        ),
    ]
