from django.db import migrations


def replace_spaceport(apps, schema_editor):
    User = apps.get_model('User', 'User')
    Inventory = apps.get_model('User', 'UserResourceInventory')

    User.objects.filter(chat_background_theme='spaceport').update(chat_background_theme='frienden-night')
    legacy = Inventory.objects.filter(
        resource_type='background',
        resource_key='spaceport',
    )
    user_ids = list(legacy.values_list('user_id', flat=True))
    legacy.update(
        reward_id='background.frienden_night',
        resource_key='frienden-night',
    )
    Inventory.objects.bulk_create([
        Inventory(
            user_id=user_id,
            resource_type='background',
            reward_id='background.frienden_garden',
            resource_key='frienden-garden',
            source='growth',
            source_reference='level:17',
        )
        for user_id in user_ids
    ], ignore_conflicts=True, batch_size=1000)


class Migration(migrations.Migration):
    dependencies = [('User', '0077_user_chat_muted_permanently_user_chat_muted_until')]
    operations = [migrations.RunPython(replace_spaceport, migrations.RunPython.noop)]
