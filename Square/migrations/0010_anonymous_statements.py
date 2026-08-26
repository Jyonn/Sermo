from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('Square', '0009_statement_forward_bundle'),
    ]

    operations = [
        migrations.AddField(
            model_name='statement',
            name='is_anonymous',
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name='statementcomment',
            name='is_anonymous',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
