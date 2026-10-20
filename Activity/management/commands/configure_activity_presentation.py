from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from Activity.models import ActivityCampaign
from Activity.presentation import presentation


class Command(BaseCommand):
    help = 'Update public campaign artwork without changing claims or collection progress.'

    def add_arguments(self, parser):
        parser.add_argument('key')
        parser.add_argument('--palette', choices=('sage', 'rose', 'ocean', 'gold'), required=True)
        parser.add_argument('--artwork-url', required=True)
        parser.add_argument('--title-art-url', required=True)
        parser.add_argument('--artwork-layout', choices=('image', 'keychain-sheet'), default='image')

    @transaction.atomic
    def handle(self, *args, **options):
        visual = {key: options[key] for key in ('palette', 'artwork_url', 'title_art_url', 'artwork_layout')}
        if presentation({'presentation': visual}) != visual:
            raise CommandError('Artwork must use root-relative or public HTTPS URLs without credentials.')
        try:
            campaign = ActivityCampaign.objects.select_for_update().get(key=options['key'])
        except ActivityCampaign.DoesNotExist:
            raise CommandError('Campaign not found.')
        campaign.config = {**campaign.config, 'presentation': visual}
        campaign.save(update_fields=['config'])
        self.stdout.write(self.style.SUCCESS('Updated presentation only. Claims and progress unchanged.'))
