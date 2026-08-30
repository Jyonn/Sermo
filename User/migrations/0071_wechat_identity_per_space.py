from django.db import migrations, models
import django.db.models.deletion


def populate_identity_space(apps, schema_editor):
    Identity = apps.get_model('User', 'WeChatMiniProgramIdentity')
    for identity in Identity.objects.select_related('user').iterator():
        identity.space_id = identity.user.space_id
        identity.save(update_fields=['space_id'])


class Migration(migrations.Migration):
    dependencies = [('User', '0070_userstateevent')]

    operations = [
        migrations.AddField(
            model_name='wechatminiprogramidentity',
            name='space',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='wechat_miniprogram_identities',
                to='Space.space',
            ),
        ),
        migrations.RunPython(populate_identity_space, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='wechatminiprogramidentity',
            name='space',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='wechat_miniprogram_identities',
                to='Space.space',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='wechatminiprogramidentity',
            name='unique_wechat_miniprogram_identity',
        ),
        migrations.AddConstraint(
            model_name='wechatminiprogramidentity',
            constraint=models.UniqueConstraint(
                fields=('app_id', 'open_id', 'space'),
                name='unique_wechat_miniprogram_identity_space',
            ),
        ),
    ]
