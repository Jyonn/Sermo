from django.db import migrations


RETIRED_CAPABILITIES = {
    'menu.personalization.bubble.use.sticker',
    'menu.personalization.bubble.use.toybrick',
}


def retire_bubble_policies(apps, schema_editor):
    PlatformPolicy = apps.get_model('AccessPolicy', 'PlatformCapabilityPolicy')
    SpacePolicy = apps.get_model('AccessPolicy', 'SpaceCapabilityPolicy')
    PlatformPolicy.objects.filter(capability_key__in=RETIRED_CAPABILITIES).delete()
    SpacePolicy.objects.filter(capability_key__in=RETIRED_CAPABILITIES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('AccessPolicy', '0003_retire_chat_background_policies'),
        ('User', '0062_retire_sticker_and_toybrick_bubbles'),
    ]
    operations = [migrations.RunPython(retire_bubble_policies, migrations.RunPython.noop)]
