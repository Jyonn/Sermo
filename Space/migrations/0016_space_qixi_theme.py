from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Space', '0015_space_research_enabled')]

    operations = [
        migrations.AddField(
            model_name='space', name='qixi_theme_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='spacefeaturegrant', name='feature_key',
            field=models.CharField(max_length=64, choices=[
                ('qq_identity_binding', 'qq_identity_binding'),
                ('oauth_developer', 'oauth_developer'),
                ('qixi_theme', 'qixi_theme'),
            ]),
        ),
    ]
