from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0091_notification_categories')]
    operations = [migrations.AddField(
        model_name='webpushsubscription', name='opted_in',
        field=models.BooleanField(default=True),
    )]
