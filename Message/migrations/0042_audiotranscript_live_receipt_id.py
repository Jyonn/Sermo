from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Message', '0041_socialparseusage')]
    operations = [migrations.AddField(model_name='audiotranscript', name='live_receipt_id', field=models.CharField(max_length=32, null=True, blank=True, unique=True))]
