from django.db import migrations


def replace_blaze_avatar_frame(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    user_model.objects.filter(avatar_frame_style='blaze').update(
        avatar_frame_style='aurora',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0036_remove_retired_chat_bubble_styles'),
    ]

    operations = [
        migrations.RunPython(replace_blaze_avatar_frame, migrations.RunPython.noop),
    ]
