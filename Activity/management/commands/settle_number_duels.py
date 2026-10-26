from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from Activity.models import NumberDuel, SpaceActivity
from Activity.number_duel import NumberDuelService


class Command(BaseCommand):
    help = 'Advance expired duel rounds and refund abandoned rooms, even with no clients online.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=500)

    def handle(self, *args, **options):
        candidates = list(NumberDuel.objects.exclude(status='finished').filter(deadline__lte=timezone.now())
                          .order_by('deadline').values_list('id', 'run_id')[:max(1, options['limit'])])
        errors = 0
        for duel_id, run_id in candidates:
            try:
                with transaction.atomic():
                    # Use the same lock order as the HTTP game service.
                    SpaceActivity.objects.select_for_update().get(id=run_id)
                    duel = NumberDuel.objects.select_related('run', 'host', 'guest').get(id=duel_id)
                    NumberDuelService.expire(duel)
            except Exception as error:
                errors += 1
                self.stderr.write(f'{duel_id}: {error}')
        self.stdout.write(f'Processed {len(candidates)} expired rooms; errors: {errors}')
        if errors:
            raise CommandError('Some duel rooms could not be settled; see errors above.')
