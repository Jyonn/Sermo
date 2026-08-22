from django.db import migrations, models


BAXIAN_REWARDS = {
    'bubble.baxian_lv': 'baxian-lv',
    'bubble.baxian_zhongli': 'baxian-zhongli',
    'bubble.baxian_he': 'baxian-he',
}


def remove_growth_baxian_rewards(apps, schema_editor):
    User = apps.get_model('User', 'User')
    Inventory = apps.get_model('User', 'UserResourceInventory')

    Inventory.objects.filter(
        reward_id__in=BAXIAN_REWARDS,
        source='growth',
    ).delete()

    for style in BAXIAN_REWARDS.values():
        owners = Inventory.objects.filter(
            resource_type='bubble',
            resource_key=style,
        ).values_list('user_id', flat=True)
        User.objects.filter(chat_bubble_style=style).exclude(id__in=owners).update(chat_bubble_style='default')


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0065_remove_legacy_bark_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userresourceinventory',
            name='source',
            field=models.CharField(
                choices=[
                    ('growth', 'growth'),
                    ('activity', 'activity'),
                    ('vip_campaign', 'vip_campaign'),
                    ('system', 'system'),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
        migrations.RunPython(remove_growth_baxian_rewards, migrations.RunPython.noop),
    ]
