from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0023_user_contact_unbound_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='bark_icon_mode',
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
