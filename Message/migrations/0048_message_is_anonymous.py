from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Message', '0047_flash_delivery_receipts')]

    operations = [
        migrations.AddField(
            model_name='message',
            name='is_anonymous',
            field=models.BooleanField(default=False, db_default=False),
        ),
    ]
