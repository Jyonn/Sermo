from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0047_heartbeat_is_explicit'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='statement_card_style',
            field=models.CharField(default='default', max_length=16),
        ),
    ]
