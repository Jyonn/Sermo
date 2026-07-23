from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0007_message_reply_to'),
    ]

    operations = [
        migrations.AddField(
            model_name='message',
            name='client_message_id',
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddConstraint(
            model_name='message',
            constraint=models.UniqueConstraint(
                fields=('chat', 'user', 'client_message_id'),
                name='message_unique_client_id',
            ),
        ),
    ]
