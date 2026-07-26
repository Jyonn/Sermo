from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0026_growth_event'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='name_changed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='plaza_greeting',
            field=models.CharField(default='', max_length=30),
        ),
    ]
