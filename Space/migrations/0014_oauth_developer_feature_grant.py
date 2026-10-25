from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('Space', '0013_space_oauth_developer_enabled')]

    operations = [
        migrations.AlterField(
            model_name='spacefeaturegrant', name='feature_key',
            field=models.CharField(max_length=64, choices=[
                ('qq_identity_binding', 'qq_identity_binding'),
                ('oauth_developer', 'oauth_developer'),
            ]),
        ),
    ]
