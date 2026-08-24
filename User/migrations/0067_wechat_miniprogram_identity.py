from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('User', '0066_move_baxian_bubbles_to_activity')]

    operations = [
        migrations.CreateModel(
            name='WeChatMiniProgramIdentity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('app_id', models.CharField(db_index=True, max_length=64)),
                ('open_id', models.CharField(max_length=128)),
                ('union_id', models.CharField(blank=True, default='', max_length=128)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='wechat_miniprogram_identities', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='wechatminiprogramidentity',
            constraint=models.UniqueConstraint(fields=('app_id', 'open_id'), name='unique_wechat_miniprogram_identity'),
        ),
    ]
