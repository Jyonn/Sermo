from django.core.management.base import BaseCommand
from Activity.models import ActivityCampaign
from Activity.number_duel import NumberDuelService


class Command(BaseCommand):
    help = 'Register the seven-day number duel for space administrators to claim; never starts a space automatically.'

    def add_arguments(self, parser):
        parser.add_argument('--enable', action='store_true')

    def handle(self, *args, **options):
        campaign, created = ActivityCampaign.objects.get_or_create(key=NumberDuelService.KEY, defaults=dict(
            title='数字交锋', title_en='Code Duel', summary='藏好四位数，和朋友来一场公平的推理对决。',
            summary_en='Hide four digits. Outsmart a friend, one fair round at a time.',
            duration_seconds=7 * 86400, event_key='number_duel.complete',
            config={'mode':NumberDuelService.MODE, 'theme':NumberDuelService.MODE,
                    'presentation':{'palette':'sage', 'artwork_url':'/activity/number-duel/digits.svg'}},
            enabled=False,
        ))
        if options['enable']:
            campaign.enabled = True
            campaign.save(update_fields=['enabled'])
        self.stdout.write(f'{campaign.key}: registered={created}; enabled={campaign.enabled}; admin claim starts 7 days.')
