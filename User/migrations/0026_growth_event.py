from django.db import migrations, models
import django.db.models.deletion


def reset_legacy_growth(apps, schema_editor):
    apps.get_model('User', 'User').objects.update(growth_score=0, growth_level=1)


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0025_user_growth'),
    ]

    operations = [
        migrations.CreateModel(
            name='GrowthEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=100)),
                ('category', models.CharField(max_length=20)),
                ('title', models.CharField(max_length=40)),
                ('points', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='growth_events', to='User.user')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'event_key'), name='user_growth_event_unique'),
                ],
            },
        ),
        migrations.RunPython(
            reset_legacy_growth,
            migrations.RunPython.noop,
        ),
    ]
