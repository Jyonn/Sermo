from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Square', '0007_squarereadstate')]

    operations = [
        migrations.AddField(
            model_name='statement', name='latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='statement', name='longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='statement', name='address',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.AddField(
            model_name='statement', name='geocoding_provider',
            field=models.CharField(blank=True, default='', max_length=32),
        ),
    ]
