from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0013_multi_user_message_pins'),
    ]

    operations = [
        migrations.AlterField(
            model_name='message',
            name='type',
            field=models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7)]),
        ),
    ]
