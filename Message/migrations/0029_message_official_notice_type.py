from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0028_mediaasset_video_transcode'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='type',
            field=models.IntegerField(choices=[
                (0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6),
                (7, 7), (8, 8), (9, 9), (10, 10), (11, 11), (12, 12),
            ]),
        ),
    ]
