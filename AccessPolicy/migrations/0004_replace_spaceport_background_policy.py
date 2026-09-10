from django.db import migrations


def replace_spaceport_policy(apps, schema_editor):
    PlatformPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    SpacePolicy = apps.get_model('AccessPolicy', 'SpaceCapabilityPolicy')
    prefix = 'menu.personalization.background.use.'

    PlatformPolicy.objects.filter(capability_key=f'{prefix}spaceport').delete()
    SpacePolicy.objects.filter(capability_key=f'{prefix}spaceport').delete()
    for asset_key in ('frienden-night', 'frienden-garden'):
        PlatformPolicy.objects.update_or_create(
            capability_key=f'{prefix}{asset_key}',
            defaults={
                'requirement': {'field': 'growth_level', 'op': 'gte', 'value': 17},
                'denial': {},
                'limits': {},
                'updated_by': 'system:migration',
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('AccessPolicy', '0008_raise_square_video_level'),
        ('User', '0078_replace_spaceport_with_frienden_rooms'),
    ]
    operations = [migrations.RunPython(replace_spaceport_policy, migrations.RunPython.noop)]
