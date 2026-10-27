from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[('Chat','0020_researchproject_and_more')]
    operations=[
        migrations.AddField(model_name='researchproject',name='calendar_policies',field=models.JSONField(default=dict)),
        migrations.AddField(model_name='researchproject',name='calendar_displays',field=models.JSONField(default=list)),
    ]
