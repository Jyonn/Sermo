from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('User', '0010_officialloginticket'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationpreference',
            name='hide_message_content',
            field=models.BooleanField(default=False),
        ),
    ]
