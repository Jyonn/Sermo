from django.db import migrations, models
import django.db.models.deletion


def copy_web_push_deliveries(apps, schema_editor):
    NotificationDelivery = apps.get_model('User', 'NotificationDelivery')
    WebPushDelivery = apps.get_model('User', 'WebPushDelivery')
    quote = schema_editor.quote_name
    destination = quote(NotificationDelivery._meta.db_table)
    source = quote(WebPushDelivery._meta.db_table)
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            f'''INSERT INTO {destination}
                (event_id, channel, web_subscription_id, status, detail, attempted_at, created_at)
                SELECT event_id, %s, subscription_id, status, detail, attempted_at, created_at
                FROM {source}''',
            [0],
        )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0068_user_profile_card_theme'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationdelivery',
            name='web_subscription',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='deliveries',
                to='User.webpushsubscription',
            ),
        ),
        migrations.RunPython(copy_web_push_deliveries, migrations.RunPython.noop),
        migrations.DeleteModel(name='WebPushDelivery'),
    ]
