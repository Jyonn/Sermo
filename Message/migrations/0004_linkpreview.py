import diq.diq
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Message', '0003_message_blob_slug'),
    ]

    operations = [
        migrations.CreateModel(
            name='LinkPreview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=2048)),
                ('url_hash', models.CharField(db_index=True, max_length=64, unique=True)),
                ('status', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2)], db_index=True, default=0)),
                ('title', models.CharField(blank=True, default='', max_length=255)),
                ('description', models.TextField(blank=True, default='')),
                ('image_url', models.URLField(blank=True, default='', max_length=2048)),
                ('site_name', models.CharField(blank=True, default='', max_length=120)),
                ('favicon_url', models.URLField(blank=True, default='', max_length=2048)),
                ('error', models.CharField(blank=True, default='', max_length=255)),
                ('fetched_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'default_manager_name': 'objects',
            },
            bases=(models.Model, diq.diq.Dictify),
        ),
    ]
