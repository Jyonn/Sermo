from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ('Activity', '0011_spaceactivity_history_backfilled_at'),
        ('User', '0078_replace_spaceport_with_frienden_rooms'),
    ]

    operations = [
        migrations.AddField(
            model_name='spaceactivity',
            name='attention_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='UserActivityView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('seen_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('space_activity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_views', to='Activity.spaceactivity')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='activity_views', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='useractivityview',
            constraint=models.UniqueConstraint(fields=('space_activity', 'user'), name='activity_space_user_view_unique'),
        ),
    ]
