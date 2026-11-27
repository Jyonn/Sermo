from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Activity', '0026_tabletop_games')]

    operations = [
        migrations.AddField(
            model_name='landlordtable', name='forfeited_position',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
    ]
