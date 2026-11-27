from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0100_drop_connection')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='square_comment_mode',
            field=models.CharField(
                choices=[('public', 'Public'), ('friends', 'Friends')],
                default='public',
                max_length=7,
            ),
        ),
    ]
