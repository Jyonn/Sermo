from django.db import migrations


def seed(apps, schema_editor):
    apps.get_model('Activity', 'ActivityCampaign').objects.update_or_create(
        key='explorer-2026', defaults={
            'title': '2026 年探索者', 'title_en': 'Explorer 2026',
            'summary': '探索五个主题，收集年度侦探档案。',
            'summary_en': 'Explore five themes and collect your annual detective rewards.',
            'assignment_mode': 'automatic', 'duration_seconds': None,
            'event_key': 'explorer.theme', 'daily_user_limit': 1, 'enabled': True,
            'config': {'theme': 'explorer', 'mode': 'annual_explorer', 'year': 2026,
                       'fixed_start': '2026-10-30T00:00:00+08:00', 'fixed_end': '2027-01-01T00:00:00+08:00',
                       'presentation': {'palette': 'ocean', 'title_art_url': '/activity/explorer-2026/title.webp',
                                        'artwork_url': '/activity/explorer-2026/conan-night.webp', 'artwork_layout': 'image'}},
        })


class Migration(migrations.Migration):
    dependencies = [('Activity', '0018_numberduel_entry_stakes'), ('User', '0093_user_signature')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
