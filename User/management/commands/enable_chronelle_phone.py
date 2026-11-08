from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from User.models import OAuthApplication
from User.oauth_registry import mutate


CALLBACK = 'https://chronelle.jyonn.space/api/profile/sermo/callback/'
ACTOR = 'frienden-external-apps-release'


class Command(BaseCommand):
    help = 'Check or enable the verified-phone scope on the existing Chronelle registration.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        app = OAuthApplication.objects.select_for_update().filter(client_id='chronelle').first()
        revision = app.approved_revision if app else None
        if (not app or app.owner_id is not None or not app.enabled or not revision
                or revision.status != 'approved' or CALLBACK not in revision.redirect_uris
                or 'profile' not in revision.scopes):
            raise CommandError('The verified Chronelle registration does not match the expected app.')
        if 'phone' in revision.scopes:
            self.stdout.write('Chronelle phone scope already approved.')
            return
        if app.revisions.filter(status__in=['draft', 'pending']).exists():
            raise CommandError('Resolve the existing Chronelle revision before changing scopes.')
        if not options['apply']:
            self.stdout.write('Ready to add Chronelle phone scope, retaining the existing registration.')
            return

        data = {field: getattr(revision, field) for field in
                ('name', 'description', 'icon_url', 'redirect_uris', 'loopback_uris')}
        data.update(action='revise', version=app.version, scopes=[*revision.scopes, 'phone'])
        outcome = mutate(actor=ACTOR, admin=True, app_id=app.id, data=data)
        draft = next(item for item in outcome['revisions'] if item['status'] == 'draft')
        outcome = mutate(actor=ACTOR, admin=True, app_id=app.id, data={
            'action': 'submit', 'version': outcome['version'], 'revision_id': draft['id'],
        })
        mutate(actor=ACTOR, admin=True, app_id=app.id, data={
            'action': 'approve', 'version': outcome['version'], 'revision_id': draft['id'],
            'approved_loopback_uris': revision.loopback_uris,
            'review_note': 'Verify one Chronelle account per verified Frienden phone across spaces.',
        })
        self.stdout.write('Chronelle phone scope approved.')
