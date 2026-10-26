from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Activity', '0016_numberduel_numberduelguess_numberduelreward')]
    operations = [
        migrations.AlterField(model_name='numberduel', name='stake', field=models.PositiveSmallIntegerField(default=0)),
        migrations.AddField(model_name='numberduel', name='host_stake', field=models.PositiveSmallIntegerField(null=True, blank=True)),
        migrations.AddField(model_name='numberduel', name='guest_stake', field=models.PositiveSmallIntegerField(null=True, blank=True)),
    ]
