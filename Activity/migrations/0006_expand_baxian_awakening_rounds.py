from django.db import migrations


CAMPAIGN_KEY = 'baxian-immortal-force-2026'


def set_awakening_count(apps, count):
    Campaign = apps.get_model('Activity', 'ActivityCampaign')
    Awakening = apps.get_model('Activity', 'ActivityAwakening')
    campaign = Campaign.objects.filter(key=CAMPAIGN_KEY).first()
    if not campaign:
        return
    config = dict(campaign.config or {})
    config['awakening_count'] = count
    campaign.config = config
    campaign.save(update_fields=['config'])
    # Awakening rows are derived from contribution history and rebuilt on the next payload.
    Awakening.objects.filter(space_activity__campaign=campaign).delete()


def forwards(apps, schema_editor):
    set_awakening_count(apps, 16)


def backwards(apps, schema_editor):
    set_awakening_count(apps, 8)


class Migration(migrations.Migration):
    dependencies = [('Activity', '0005_activityevent_claimed_at')]
    operations = [migrations.RunPython(forwards, backwards)]
