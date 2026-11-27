import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [('Activity', '0028_activity_editions')]

    operations = [
        migrations.CreateModel(
            name='ActivityRerunRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')],
                                            db_index=True, default='pending', max_length=10)),
                ('submitted_at', models.DateTimeField(default=timezone.now)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('previous_run', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                     related_name='rerun_request', to='Activity.spaceactivity')),
            ],
        ),
    ]
