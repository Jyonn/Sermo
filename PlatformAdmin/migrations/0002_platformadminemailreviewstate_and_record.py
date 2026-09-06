from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('PlatformAdmin', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PlatformAdminEmailReviewState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('singleton_key', models.CharField(default='primary', max_length=16, unique=True)),
                ('enabled', models.BooleanField(default=False)),
                ('captured_count', models.PositiveSmallIntegerField(default=0)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name='PlatformAdminEmailReviewRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sequence', models.PositiveSmallIntegerField()),
                ('recipient', models.TextField(blank=True, default='')),
                ('mail_format', models.CharField(blank=True, default='', max_length=32)),
                ('title', models.TextField(blank=True, default='')),
                ('body', models.JSONField(blank=True, null=True)),
                ('body_text', models.TextField(blank=True, default='')),
                ('locale', models.CharField(blank=True, default='', max_length=32)),
                ('recipient_name', models.CharField(blank=True, default='', max_length=255)),
                ('action_url', models.TextField(blank=True, default='')),
                ('footer_note', models.TextField(blank=True, default='')),
                ('status', models.CharField(choices=[('processing', 'processing'), ('sent', 'sent'), ('failed', 'failed')], default='processing', max_length=16)),
                ('detail', models.TextField(blank=True, default='')),
                ('request_id', models.CharField(blank=True, default='', max_length=255)),
                ('provider_response', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('state', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='PlatformAdmin.platformadminemailreviewstate')),
            ],
            options={'ordering': ['-sequence']},
        ),
        migrations.AddConstraint(
            model_name='platformadminemailreviewrecord',
            constraint=models.UniqueConstraint(fields=('state', 'sequence'), name='unique_platform_email_review_sequence'),
        ),
    ]
