from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0017_usergesturelockpreference'),
    ]

    operations = [
        migrations.AddField(
            model_name='usergesturelockpreference',
            name='decoy_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='usergesturelockpreference',
            name='decoy_pattern_hash',
            field=models.CharField(blank=True, default='', max_length=128),
        ),
        migrations.AddField(
            model_name='usergesturelockpreference',
            name='decoy_salt',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
