from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('PlatformAdmin','0003_clientdebugreport')]
    operations = [
        migrations.CreateModel(name='FrontendRelease',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('version',models.CharField(max_length=32,unique=True)),('notes',models.JSONField()),('published_on',models.DateField()),('created_at',models.DateTimeField(auto_now_add=True))]),
        migrations.CreateModel(name='FrontendDeployment',fields=[('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),('origin',models.URLField(unique=True)),('updated_at',models.DateTimeField(auto_now=True)),('release',models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,related_name='deployments',to='PlatformAdmin.frontendrelease'))]),
    ]
