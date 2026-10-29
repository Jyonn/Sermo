from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0092_webpush_account_opt_in')]
    operations = [migrations.AddField(
        model_name='user', name='signature',
        field=models.CharField(max_length=80, blank=True, default=''),
    )]
