import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0030_welcomemessagetemplate'),
    ]

    operations = [
        migrations.CreateModel(
            name='AudioTranscript',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2)], default=0)),
                ('text', models.TextField(blank=True, default='')),
                ('provider_request_id', models.CharField(blank=True, default='', max_length=128)),
                ('provider_error', models.CharField(blank=True, default='', max_length=255)),
                ('started_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('message', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='audio_transcript', to='Message.message')),
            ],
            options={
                'default_manager_name': 'objects',
            },
        ),
    ]
