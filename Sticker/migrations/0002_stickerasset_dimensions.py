from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Sticker', '0001_initial')]
    operations = [
        migrations.AddField(
            model_name='stickerasset',
            name='pixel_width',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stickerasset',
            name='pixel_height',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stickerasset',
            name='dimensions_checked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='stickerasset',
            name='dimensions_error',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
