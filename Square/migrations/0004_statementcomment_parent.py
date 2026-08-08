from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('Square', '0003_square_social_media')]

    operations = [
        migrations.AddField(
            model_name='statementcomment',
            name='parent',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='replies', to='Square.statementcomment'),
        ),
    ]
