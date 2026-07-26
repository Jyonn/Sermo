from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Friendship', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='friendship',
            name='source',
            field=models.CharField(blank=True, default='', max_length=16),
        ),
    ]
