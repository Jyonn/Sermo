from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0048_user_statement_card_style'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='pinned_square_statement_id',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
