from django.db import migrations


ACTIVE_BACKGROUNDS = {
    'default': 1,
    'paper': 2,
    'mint': 3,
    'comic': 5,
    'zen': 6,
    'dragon': 8,
    'bauhaus': 8,
    'mosaic': 9,
    'aurora-sky': 14,
    'newsprint': 15,
    'hologram': 15,
    'spaceport': 17,
    'noir-film': 18,
    'custom': 8,
}


def refresh_background_policies(apps, schema_editor):
    PlatformPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    SpacePolicy = apps.get_model('AccessPolicy', 'SpaceCapabilityPolicy')
    prefix = 'menu.personalization.background.use.'
    active_keys = {f'{prefix}{key}' for key in ACTIVE_BACKGROUNDS}

    PlatformPolicy.objects.filter(capability_key__startswith=prefix).exclude(capability_key__in=active_keys).delete()
    SpacePolicy.objects.filter(capability_key__startswith=prefix).exclude(capability_key__in=active_keys).delete()
    for asset_key, level in ACTIVE_BACKGROUNDS.items():
        PlatformPolicy.objects.update_or_create(
            capability_key=f'{prefix}{asset_key}',
            defaults={
                'requirement': {'field': 'growth_level', 'op': 'gte', 'value': level},
                'denial': {},
                'limits': {},
                'updated_by': 'system:migration',
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('AccessPolicy', '0002_seed_platform_baseline_policies'),
        ('User', '0061_retire_chat_backgrounds'),
    ]
    operations = [migrations.RunPython(refresh_background_policies, migrations.RunPython.noop)]
