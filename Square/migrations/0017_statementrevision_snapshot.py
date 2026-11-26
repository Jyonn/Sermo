from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Square', '0016_statement_edit_revision_statement_edited_at_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='statementrevision',
            name='snapshot',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
