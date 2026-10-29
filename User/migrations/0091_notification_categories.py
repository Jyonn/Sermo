from django.db import migrations, models


def unify(apps,schema_editor):
    Pref=apps.get_model('User','NotificationTopicPreference')
    for old in Pref.objects.filter(topic__in=[2,3,4,5],audience=1).iterator():
        Pref.objects.update_or_create(user_id=old.user_id,channel=old.channel,topic=old.topic,audience=0,defaults={'enabled':old.enabled})
    for old in Pref.objects.filter(topic=1,audience=0).iterator():
        for topic in (7,10):
            Pref.objects.get_or_create(user_id=old.user_id,channel=old.channel,topic=topic,audience=0,defaults={'enabled':old.enabled})


class Migration(migrations.Migration):
    dependencies=[('User','0090_chronellecredential')]
    operations=[migrations.AlterField(model_name='notificationtopicpreference',name='topic',field=models.IntegerField(choices=[(i,i) for i in range(1,12)])),
        migrations.RunPython(unify,migrations.RunPython.noop)]
