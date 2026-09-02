from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('Chat', '0009_submission_author_submission_published_statement_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='submission',
            name='status',
            field=models.IntegerField(
                choices=[(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6)],
                db_index=True,
                default=0,
            ),
        ),
    ]
