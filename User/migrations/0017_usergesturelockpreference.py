import diq.diq
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0016_userwebreminderpreference'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserGestureLockPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('enabled', models.BooleanField(default=False)),
                ('pattern_hash', models.CharField(blank=True, default='', max_length=128)),
                ('salt', models.CharField(blank=True, default='', max_length=64)),
                ('lock_after_minutes', models.PositiveSmallIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='gesture_lock_preference', to='User.user')),
            ],
            options={
                'abstract': False,
                'default_manager_name': 'objects',
            },
            bases=(models.Model, diq.diq.Dictify),
        ),
    ]
