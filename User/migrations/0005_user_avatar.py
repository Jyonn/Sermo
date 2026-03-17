from django.db import migrations, models


AVATAR_PRESET_BASE_URI = 'https://image.6-79.cn/sermo/assets/avatars'
AVATAR_PRESET_MIN_ID = 1
AVATAR_PRESET_MAX_ID = 80


def fill_avatar_for_existing_users(apps, schema_editor):
    User = apps.get_model('User', 'User')
    span = AVATAR_PRESET_MAX_ID - AVATAR_PRESET_MIN_ID + 1
    for user in User.objects.all().only('id').iterator():
        preset_id = ((user.id - 1) % span) + AVATAR_PRESET_MIN_ID
        user.avatar_type = 'preset'
        user.avatar_uri = f'{AVATAR_PRESET_BASE_URI}/{preset_id:02d}.svg'
        user.save(update_fields=['avatar_type', 'avatar_uri'])


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0004_user_welcome_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='avatar_type',
            field=models.CharField(
                choices=[('preset', 'preset'), ('custom', 'custom')],
                default='preset',
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='avatar_uri',
            field=models.CharField(
                default=f'{AVATAR_PRESET_BASE_URI}/01.svg',
                max_length=255,
            ),
        ),
        migrations.RunPython(fill_avatar_for_existing_users, migrations.RunPython.noop),
    ]
