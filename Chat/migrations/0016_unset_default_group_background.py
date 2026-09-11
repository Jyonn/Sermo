from django.db import migrations, models


def unset_legacy_default_backgrounds(apps, schema_editor):
    apps.get_model('Chat', 'Chat').objects.filter(group_background_theme='default').update(
        group_background_theme='',
    )


class Migration(migrations.Migration):
    dependencies = [('Chat', '0015_group_chat_backgrounds')]

    operations = [
        migrations.AlterField(
            model_name='chat',
            name='group_background_theme',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
        migrations.RunPython(unset_legacy_default_backgrounds, migrations.RunPython.noop),
    ]
