from django.db import migrations


FRAME_REPLACEMENTS = {
    'pixel': 'camera',
    'vine': 'blossom',
    'coral': 'papercut',
    'radar': 'portal',
    'pulse': 'soundwave',
    'stamp': 'polaroid',
    'embroidery': 'lucky',
    'stainedglass': 'papercut',
}


def replace_retired_avatar_frames(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    for retired, replacement in FRAME_REPLACEMENTS.items():
        user_model.objects.filter(avatar_frame_style=retired).update(
            avatar_frame_style=replacement,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0037_replace_blaze_avatar_frame'),
    ]

    operations = [
        migrations.RunPython(
            replace_retired_avatar_frames,
            migrations.RunPython.noop,
        ),
    ]
