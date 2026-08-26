from django.db import migrations


CAPABILITY_KEY = 'square.statement.publish.video'


def requirement(level):
    return {'field': 'growth_level', 'op': 'gte', 'value': level}


def set_video_level(apps, level):
    Policy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    Policy.objects.update_or_create(
        capability_key=CAPABILITY_KEY,
        defaults={
            'requirement': requirement(level),
            'denial': {},
            'limits': {},
            'updated_by': 'system:migration',
        },
    )


def raise_video_level(apps, schema_editor):
    set_video_level(apps, 10)


def restore_video_level(apps, schema_editor):
    set_video_level(apps, 8)


class Migration(migrations.Migration):
    dependencies = [('AccessPolicy', '0007_wechat_miniprogram_profile_capabilities')]

    operations = [migrations.RunPython(raise_video_level, restore_video_level)]
