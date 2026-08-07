from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Square', '0001_initial'), ('User', '0001_initial')]

    operations = [
        migrations.CreateModel(
            name='StatementComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(max_length=140)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('is_deleted', models.BooleanField(db_index=True, default=False)),
                ('statement', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='Square.statement')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='statement_comments', to='User.user')),
            ],
            options={'ordering': ['-id']},
        ),
    ]
