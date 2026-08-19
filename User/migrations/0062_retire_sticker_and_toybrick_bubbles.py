from django.db import migrations


RETIRED_BUBBLES = {'sticker', 'toybrick'}
RETIRED_REWARDS = {'bubble.sticker', 'bubble.toybrick'}


def retire_bubbles(apps, schema_editor):
    User = apps.get_model('User', 'User')
    Inventory = apps.get_model('User', 'UserResourceInventory')

    User.objects.filter(chat_bubble_style__in=RETIRED_BUBBLES).update(chat_bubble_style='default')
    Inventory.objects.filter(resource_type='bubble').filter(
        resource_key__in=RETIRED_BUBBLES,
    ).delete()
    Inventory.objects.filter(reward_id__in=RETIRED_REWARDS).delete()


class Migration(migrations.Migration):
    dependencies = [('User', '0061_retire_chat_backgrounds')]
    operations = [migrations.RunPython(retire_bubbles, migrations.RunPython.noop)]
