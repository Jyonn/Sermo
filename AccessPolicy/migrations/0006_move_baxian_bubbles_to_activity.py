from django.db import migrations


BAXIAN_CAPABILITIES = {
    'menu.personalization.bubble.use.baxian-lv',
    'menu.personalization.bubble.use.baxian-zhongli',
    'menu.personalization.bubble.use.baxian-he',
}


def remove_level_policies(apps, schema_editor):
    PlatformPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    SpacePolicy = apps.get_model('AccessPolicy', 'SpaceCapabilityPolicy')
    PlatformPolicy.objects.filter(capability_key__in=BAXIAN_CAPABILITIES).delete()
    SpacePolicy.objects.filter(capability_key__in=BAXIAN_CAPABILITIES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('AccessPolicy', '0005_refine_avatar_frame_policies'),
        ('User', '0066_move_baxian_bubbles_to_activity'),
    ]

    operations = [
        migrations.RunPython(remove_level_policies, migrations.RunPython.noop),
    ]
