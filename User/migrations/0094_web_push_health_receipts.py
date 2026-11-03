from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0093_user_signature')]
    operations = [
        migrations.AddField(model_name='webpushsubscription', name='invalidated_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='notificationdelivery', name='client_received_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='notificationdelivery', name='notification_submitted_at', field=models.DateTimeField(null=True, blank=True)),
        migrations.AddField(model_name='notificationdelivery', name='client_error', field=models.CharField(max_length=64, null=True, blank=True, default='')),
    ]
