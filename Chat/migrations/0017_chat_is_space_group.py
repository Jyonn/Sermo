from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Chat', '0016_unset_default_group_background'),
        ('Space', '0012_space_space_group_enabled'),
    ]
    operations = [
        migrations.AddField(
            model_name='chat',
            name='is_space_group',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
