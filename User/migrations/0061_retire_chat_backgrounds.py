from django.db import migrations


ACTIVE_BACKGROUNDS = {
    'default': (1, 'background.default'),
    'paper': (2, 'background.paper'),
    'mint': (3, 'background.mint'),
    'comic': (5, 'background.comic'),
    'zen': (6, 'background.zen'),
    'dragon': (8, 'background.dragon'),
    'bauhaus': (8, 'background.bauhaus'),
    'mosaic': (9, 'background.mosaic'),
    'aurora-sky': (14, 'background.aurora_sky'),
    'newsprint': (15, 'background.newsprint'),
    'hologram': (15, 'background.hologram'),
    'spaceport': (17, 'background.spaceport'),
    'noir-film': (18, 'background.noir'),
}


def retire_chat_backgrounds(apps, schema_editor):
    User = apps.get_model('User', 'User')
    Inventory = apps.get_model('User', 'UserResourceInventory')
    active_keys = set(ACTIVE_BACKGROUNDS) | {'custom'}

    User.objects.exclude(chat_background_theme__in=active_keys).update(
        chat_background_theme='default',
        chat_background_uri='',
    )
    Inventory.objects.filter(resource_type='background').exclude(
        resource_key__in=ACTIVE_BACKGROUNDS,
    ).delete()

    rows = []
    for user in User.objects.filter(is_deleted=False).iterator():
        level = 18 if user.role == 0 else max(1, user.growth_level, user.growth_acknowledged_level)
        for resource_key, (reward_level, reward_id) in ACTIVE_BACKGROUNDS.items():
            if reward_level <= level:
                rows.append(Inventory(
                    user_id=user.id,
                    resource_type='background',
                    reward_id=reward_id,
                    resource_key=resource_key,
                    source='growth',
                    source_reference=f'level:{reward_level}',
                ))
    Inventory.objects.bulk_create(rows, ignore_conflicts=True, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [('User', '0060_instantnotificationendpoint_and_more')]
    operations = [migrations.RunPython(retire_chat_backgrounds, migrations.RunPython.noop)]
