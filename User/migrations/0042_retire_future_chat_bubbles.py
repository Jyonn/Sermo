from django.db import migrations


def replace_future_chat_bubbles(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    user_model.objects.filter(chat_bubble_style='terminal').update(
        chat_bubble_style='typewriter',
    )
    user_model.objects.filter(chat_bubble_style='hologram').update(
        chat_bubble_style='mosaic',
    )
    user_model.objects.filter(chat_bubble_style='mech').update(
        chat_bubble_style='toybrick',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0041_retire_low_identity_chat_bubbles'),
    ]

    operations = [
        migrations.RunPython(
            replace_future_chat_bubbles,
            migrations.RunPython.noop,
        ),
    ]
