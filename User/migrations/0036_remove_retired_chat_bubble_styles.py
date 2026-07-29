from django.db import migrations


def reset_retired_bubble_styles(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    user_model.objects.filter(chat_bubble_style__in=('tide', 'neon')).update(
        chat_bubble_style='default',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0035_user_language_preference'),
    ]

    operations = [
        migrations.RunPython(reset_retired_bubble_styles, migrations.RunPython.noop),
    ]
