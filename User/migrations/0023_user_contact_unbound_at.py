from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0022_user_private_account_and_switch_ticket'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='email_unbound_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='phone_unbound_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='bark_unbound_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
