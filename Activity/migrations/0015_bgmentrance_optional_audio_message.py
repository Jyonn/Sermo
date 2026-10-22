from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Activity', '0014_bgmparticipation_mode_bgmparticipation_receive_audio_and_more')]

    operations = [
        migrations.AlterField(
            model_name='bgmentrance',
            name='audio_message_id',
            field=models.PositiveBigIntegerField(null=True, blank=True),
        ),
    ]
