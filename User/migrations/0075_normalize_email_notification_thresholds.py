from django.db import migrations


EMAIL_CHANNEL = 1
EMAIL_THRESHOLD_OPTIONS = (10, 20, 30, 60, 120, 180, 360, 720, 1440)


def normalize_email_notification_thresholds(apps, schema_editor):
    NotificationPreference = apps.get_model('User', 'NotificationPreference')
    preferences = NotificationPreference.objects.filter(channel=EMAIL_CHANNEL).only(
        'id',
        'offline_threshold_minutes',
    )
    updates = []
    for preference in preferences.iterator(chunk_size=500):
        current = preference.offline_threshold_minutes
        normalized = next(
            (option for option in EMAIL_THRESHOLD_OPTIONS if option >= current),
            EMAIL_THRESHOLD_OPTIONS[-1],
        )
        if normalized != current:
            preference.offline_threshold_minutes = normalized
            updates.append(preference)
        if len(updates) >= 500:
            NotificationPreference.objects.bulk_update(
                updates,
                ['offline_threshold_minutes'],
                batch_size=500,
            )
            updates.clear()
    if updates:
        NotificationPreference.objects.bulk_update(
            updates,
            ['offline_threshold_minutes'],
            batch_size=500,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('User', '0074_harden_notification_delivery_dispatch'),
    ]

    operations = [
        migrations.RunPython(
            normalize_email_notification_thresholds,
            migrations.RunPython.noop,
        ),
    ]
