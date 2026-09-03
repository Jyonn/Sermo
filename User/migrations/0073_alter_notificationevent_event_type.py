from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0072_alter_userresourceinventory_resource_type')]

    operations = [
        migrations.AlterField(
            model_name='notificationevent',
            name='event_type',
            field=models.IntegerField(
                choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10)],
                db_index=True,
            ),
        ),
    ]
