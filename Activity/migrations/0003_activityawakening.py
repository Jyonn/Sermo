from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Activity', '0002_seed_baxian_activity'),
        ('User', '0066_move_baxian_bubbles_to_activity'),
    ]

    operations = [
        migrations.CreateModel(
            name='ActivityAwakening',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('step', models.PositiveSmallIntegerField()),
                ('threshold', models.PositiveIntegerField()),
                ('unlocked_at', models.DateTimeField(auto_now_add=True)),
                ('space_activity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='awakenings', to='Activity.spaceactivity')),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='activity_awakenings', to='User.user')),
            ],
            options={'ordering': ['step']},
        ),
        migrations.AddConstraint(
            model_name='activityawakening',
            constraint=models.UniqueConstraint(fields=('space_activity', 'step'), name='activity_space_awakening_unique'),
        ),
    ]
