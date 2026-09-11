from django.core.management.base import BaseCommand, CommandError

from Activity.models import ActivityService, SpaceActivity


class Command(BaseCommand):
    help = 'Backfill pre-claim evening chats for existing Starry Night space activities.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--campaign-key',
            default='starry-night-five-evenings-2026',
            help='Only process this activity campaign key.',
        )
        parser.add_argument(
            '--space-slug',
            help='Only process the matching space.',
        )

    def handle(self, *args, **options):
        runs = SpaceActivity.objects.filter(
            campaign__key=options['campaign_key'],
            campaign__config__mode=ActivityService.STARRY_NIGHT_MODE,
        ).select_related('campaign', 'space').order_by('id')
        if options.get('space_slug'):
            runs = runs.filter(space__slug=options['space_slug'])
        if not runs.exists():
            raise CommandError('No matching claimed Starry Night activities were found.')

        totals = {'spaces': 0, 'eligible_users': 0, 'events_created': 0, 'rewards_created': 0}
        for run in runs.iterator():
            result = ActivityService.backfill_starry_night_progress(run, run.claimed_at)
            totals['spaces'] += 1
            for key in ('eligible_users', 'events_created', 'rewards_created'):
                totals[key] += result[key]
            self.stdout.write(
                f'{run.space.slug}: users={result["eligible_users"]}, '
                f'events={result["events_created"]}, rewards={result["rewards_created"]}'
            )
        self.stdout.write(self.style.SUCCESS(
            'Completed: spaces={spaces}, users={eligible_users}, events={events_created}, '
            'rewards={rewards_created}'.format(**totals)
        ))
