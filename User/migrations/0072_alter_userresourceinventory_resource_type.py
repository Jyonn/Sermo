from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0071_wechat_identity_per_space')]

    operations = [
        migrations.AlterField(
            model_name='userresourceinventory',
            name='resource_type',
            field=models.CharField(
                choices=[
                    ('background', 'background'), ('bubble', 'bubble'), ('frame', 'frame'),
                    ('statement', 'statement'), ('identity', 'identity'), ('vip', 'vip'),
                    ('profile', 'profile'),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
    ]
