from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Square', '0002_statementcomment'), ('User', '0001_initial')]

    operations = [
        migrations.AddField(model_name='statementmedia', name='metadata', field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name='statementmedia', name='metadata_error', field=models.CharField(blank=True, default='', max_length=500)),
        migrations.AddField(model_name='statementmedia', name='metadata_status', field=models.IntegerField(db_index=True, default=0)),
        migrations.AlterField(model_name='statementmedia', name='kind', field=models.IntegerField(choices=[(0, 0), (1, 1), (2, 2)])),
        migrations.CreateModel(
            name='StatementLike',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('statement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='likes', to='Square.statement')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statement_likes', to='User.user')),
            ],
        ),
        migrations.CreateModel(
            name='StatementCommentLike',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('comment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='likes', to='Square.statementcomment')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statement_comment_likes', to='User.user')),
            ],
        ),
        migrations.AddConstraint(model_name='statementlike', constraint=models.UniqueConstraint(fields=('statement', 'user'), name='unique_statement_like')),
        migrations.AddConstraint(model_name='statementcommentlike', constraint=models.UniqueConstraint(fields=('comment', 'user'), name='unique_statement_comment_like')),
    ]
