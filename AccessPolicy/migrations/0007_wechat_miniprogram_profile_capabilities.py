from django.db import migrations


CAPABILITY_LEVELS = {
    'menu.profile.avatar.custom': 4,
    'menu.profile.nickname': 5,
}


def allow_wechat_profiles(apps, schema_editor):
    Policy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    for key, level in CAPABILITY_LEVELS.items():
        Policy.objects.filter(capability_key=key).update(requirement={
            'any': [
                {'field': 'growth_level', 'op': 'gte', 'value': level},
                {'field': 'wechat_miniprogram', 'op': 'eq', 'value': True},
            ],
        })


def restore_growth_requirements(apps, schema_editor):
    Policy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    for key, level in CAPABILITY_LEVELS.items():
        Policy.objects.filter(capability_key=key).update(requirement={
            'field': 'growth_level', 'op': 'gte', 'value': level,
        })


class Migration(migrations.Migration):
    dependencies = [
        ('AccessPolicy', '0006_move_baxian_bubbles_to_activity'),
        ('User', '0067_wechat_miniprogram_identity'),
    ]

    operations = [
        migrations.RunPython(allow_wechat_profiles, restore_growth_requirements),
    ]
