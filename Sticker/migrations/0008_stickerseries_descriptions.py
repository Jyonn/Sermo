from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Sticker', '0007_single_collection_membership')]
    operations = [migrations.AddField(model_name='stickerseries', name='descriptions', field=models.JSONField(default=dict))]
