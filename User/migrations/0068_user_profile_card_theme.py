from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('User', '0067_wechat_miniprogram_identity')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='profile_card_theme',
            field=models.CharField(default='default', max_length=16),
        ),
    ]
