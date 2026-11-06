from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Chat', '0026_research_workflow')]
    operations = [
        migrations.AlterField(
            model_name='submissioninvite', name='status',
            field=models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3)], db_index=True, default=0),
        ),
    ]
