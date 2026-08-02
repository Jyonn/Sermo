from django.db import migrations


BUBBLE_REPLACEMENTS = {
    'pebble': 'zen',
    'leaf': 'zen',
    'cloud': 'hologram',
    'ice': 'hologram',
    'lava': 'dragon',
    'postcard': 'newspaper',
    'blueprint': 'typewriter',
    'synthwave': 'hologram',
    'orbital': 'mech',
    'candy': 'sticker',
    'doodle': 'comic',
    'plush': 'sticker',
}


def replace_retired_chat_bubbles(apps, schema_editor):
    user_model = apps.get_model('User', 'User')
    for retired, replacement in BUBBLE_REPLACEMENTS.items():
        user_model.objects.filter(chat_bubble_style=retired).update(
            chat_bubble_style=replacement,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0040_retire_nature_avatar_frames'),
    ]

    operations = [
        migrations.RunPython(
            replace_retired_chat_bubbles,
            migrations.RunPython.noop,
        ),
    ]
