from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0078_replace_spaceport_with_frienden_rooms')]
    operations = [
        migrations.AddField(
            model_name='user',
            name='left_space_group_manually',
            field=models.BooleanField(default=False),
        ),
    ]
