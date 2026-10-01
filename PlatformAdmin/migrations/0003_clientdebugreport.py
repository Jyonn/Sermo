from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('PlatformAdmin', '0002_platformadminemailreviewstate_and_record'),
        ('User', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ClientDebugReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('report', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='debug_reports', to='User.user')),
            ],
            options={'ordering': ['-created_at', '-id']},
        ),
    ]
