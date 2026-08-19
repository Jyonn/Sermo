from django.db import migrations


RETIRED_FRAMES = {
    'camera', 'soundwave', 'moon', 'portal', 'snowfall', 'comet', 'butterfly',
}
RETIRED_REWARDS = {
    'frame.camera', 'frame.soundwave', 'frame.moon', 'frame.portal',
    'frame.snowfall', 'frame.comet', 'frame.butterfly',
}


def refine_avatar_frames(apps, schema_editor):
    User = apps.get_model('User', 'User')
    Inventory = apps.get_model('User', 'UserResourceInventory')

    User.objects.filter(avatar_frame_style__in=RETIRED_FRAMES).update(avatar_frame_style='none')
    Inventory.objects.filter(resource_type='frame', resource_key__in=RETIRED_FRAMES).delete()
    Inventory.objects.filter(reward_id__in=RETIRED_REWARDS).delete()


class Migration(migrations.Migration):
    dependencies = [('User', '0062_retire_sticker_and_toybrick_bubbles')]
    operations = [migrations.RunPython(refine_avatar_frames, migrations.RunPython.noop)]
