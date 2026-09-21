from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0034_message_submission_round_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='linkpreview',
            name='provider_data',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
