from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0045_permanent_vip_growth_reward')]

    operations = [
        migrations.AlterField(
            model_name='notificationevent',
            name='event_type',
            field=models.IntegerField(
                choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8)],
                db_index=True,
            ),
        ),
        migrations.CreateModel(
            name='NotificationTopicPreference',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)])),
                ('topic', models.IntegerField(choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)])),
                ('audience', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2)], default=0)),
                ('enabled', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='notification_topic_preferences',
                    to='User.user',
                )),
            ],
        ),
        migrations.AddConstraint(
            model_name='notificationtopicpreference',
            constraint=models.UniqueConstraint(
                fields=('user', 'channel', 'topic', 'audience'),
                name='unique_notification_topic_pref',
            ),
        ),
    ]
