from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('Square', '0012_squaremute'),
        ('Sticker', '0002_stickerasset_dimensions'),
        ('User', '0073_alter_notificationevent_event_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='statementcomment',
            name='text',
            field=models.CharField(blank=True, default='', max_length=140),
        ),
        migrations.AddField(
            model_name='statementcomment',
            name='sticker_asset',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='statement_comments',
                to='Sticker.stickerasset',
            ),
        ),
        migrations.CreateModel(
            name='StatementCommentMention',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('comment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comment_mentions', to='Square.statementcomment')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statement_comment_mentions', to='User.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='statementcommentmention',
            constraint=models.UniqueConstraint(fields=('comment', 'user'), name='unique_statement_comment_mention_user'),
        ),
    ]
