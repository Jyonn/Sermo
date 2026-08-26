from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Square', '0010_anonymous_statements')]

    operations = [
        migrations.AddField(
            model_name='statement',
            name='chat_record_redacted',
            field=models.BooleanField(default=False),
        ),
    ]
