from django.db import migrations


RETIRED_CAPABILITIES = {
    f'menu.personalization.frame.use.{style}'
    for style in ('camera', 'soundwave', 'moon', 'portal', 'snowfall', 'comet', 'butterfly')
}


def refine_avatar_frame_policies(apps, schema_editor):
    PlatformPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    SpacePolicy = apps.get_model('AccessPolicy', 'SpaceCapabilityPolicy')
    PlatformPolicy.objects.filter(capability_key__in=RETIRED_CAPABILITIES).delete()
    SpacePolicy.objects.filter(capability_key__in=RETIRED_CAPABILITIES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('AccessPolicy', '0004_retire_sticker_and_toybrick_bubble_policies'),
        ('User', '0063_refine_avatar_frame_catalog'),
    ]
    operations = [migrations.RunPython(refine_avatar_frame_policies, migrations.RunPython.noop)]
