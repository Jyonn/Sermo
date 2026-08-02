from django.db import migrations


def replace_retired_nature_frames(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    user_model.objects.filter(avatar_frame_style='blossom').update(
        avatar_frame_style='butterfly',
    )
    user_model.objects.filter(avatar_frame_style='firefly').update(
        avatar_frame_style='moon',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0039_retire_accessory_avatar_frames'),
    ]

    operations = [
        migrations.RunPython(
            replace_retired_nature_frames,
            migrations.RunPython.noop,
        ),
    ]
