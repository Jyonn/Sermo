import django.db.models.deletion
from django.db import migrations, models


def migrate_legacy_welcome_messages(apps, schema_editor):
    User = apps.get_model('User', 'User')
    WelcomeMessageTemplate = apps.get_model('Message', 'WelcomeMessageTemplate')
    templates = []
    for user in User.objects.exclude(welcome_message='').iterator():
        message = (user.welcome_message or '').strip()
        if message:
            templates.append(WelcomeMessageTemplate(
                user_id=user.id,
                position=0,
                type=0,
                content=message,
            ))
    WelcomeMessageTemplate.objects.bulk_create(templates, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ('Message', '0029_message_official_notice_type'),
        ('User', '0072_alter_userresourceinventory_resource_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='WelcomeMessageTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('position', models.PositiveSmallIntegerField(default=0)),
                ('type', models.IntegerField(choices=[(0, 0), (1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11), (12, 12)])),
                ('content', models.CharField(blank=True, default='', max_length=512)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('media_resource', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='welcome_templates', to='Message.mediaresource')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='welcome_message_templates', to='User.user')),
            ],
            options={'ordering': ['position', 'id']},
        ),
        migrations.AddConstraint(
            model_name='welcomemessagetemplate',
            constraint=models.UniqueConstraint(fields=('user', 'position'), name='welcome_template_unique_position'),
        ),
        migrations.RunPython(migrate_legacy_welcome_messages, migrations.RunPython.noop),
    ]
