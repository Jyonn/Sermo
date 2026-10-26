from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Activity', '0017_numberduel_tier_agreement'), ('User', '0088_growth_event_signed_points')]
    operations = [
        migrations.AddField(model_name='numberduel', name='funded', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='numberduel', name='scoring_date', field=models.DateField(null=True, blank=True)),
        migrations.AddField(model_name='numberduel', name='practice_reason', field=models.CharField(max_length=16, blank=True, default='')),
        migrations.AlterField(model_name='numberduelreward', name='points', field=models.SmallIntegerField(default=0)),
    ]
