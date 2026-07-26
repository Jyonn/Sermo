from django.db import migrations, models

import Space.models

LEVEL_NAMES = [
    '初见', '起言', '同频', '渐熟', '热聊', '成群',
    '入浪', '逐潮', '回响', '共鸣', '风生', '云起',
    '浪涌', '潮生', '星聚', '盛放', '无界', '尽兴',
]


def expand_level_names(apps, schema_editor):
    Space = apps.get_model('Space', 'Space')
    for space in Space.objects.all().only('id', 'level_names'):
        current = space.level_names or []
        if len(current) != 18:
            space.level_names = LEVEL_NAMES
            space.save(update_fields=['level_names'])


class Migration(migrations.Migration):
    dependencies = [
        ('Space', '0004_space_level_names'),
    ]

    operations = [
        migrations.AlterField(
            model_name='space',
            name='level_names',
            field=models.JSONField(default=Space.models.default_level_names),
        ),
        migrations.RunPython(expand_level_names, migrations.RunPython.noop),
    ]
