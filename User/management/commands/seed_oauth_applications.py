"""Explicit, atomic import of the three legacy registrations; never edits grants."""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from User.models import OAuthApplication, OAuthGrant
from User.oauth_registry import configuration, mutate, RegistryError


def seed(stillfolio_uri):
    records = [
        ('stillfolio', 'StillFolio', stillfolio_uri, ['phone', 'profile', 'vip']),
        ('chronelle', 'Chronelle', 'https://chronelle.jyonn.space/api/profile/sermo/callback/', ['profile']),
        ('supervision-local', 'Supervision', 'http://127.0.0.1:8001/api/auth/friendom/callback/', ['profile']),
    ]
    with transaction.atomic():
        for client_id, name, uri, scopes in records:
            config = configuration(dict(name=name, redirect_uris=[uri], scopes=scopes,
                                        loopback_uris=[uri] if uri.startswith('http:') else []))
            # A mismatch must be investigated, never silently broadened or overwritten.
            for grant in OAuthGrant.objects.filter(client_id=client_id).only('redirect_uri', 'scopes'):
                if grant.redirect_uri != uri or not set(grant.scopes) <= set(scopes):
                    raise CommandError(f'Legacy grant conflict for {client_id}; nothing imported')
            app = OAuthApplication.objects.select_related('approved_revision').filter(client_id=client_id).first()
            if app:
                rev = app.approved_revision
                if not app.enabled or app.owner_id is not None or not rev or rev.status != 'approved' or any(getattr(rev, k) != v for k, v in config.items()):
                    raise CommandError(f'Registry conflict for {client_id}; nothing imported')
                continue
            mutate(actor='legacy-import', admin=True, data=dict(action='create', client_id=client_id, **config))


class Command(BaseCommand):
    help = 'Import legacy OAuth clients. Requires the explicitly confirmed existing StillFolio callback.'

    def add_arguments(self, parser):
        parser.add_argument('--stillfolio-redirect-uri', required=True)

    def handle(self, *args, **options):
        try:
            seed(options['stillfolio_redirect_uri'])
        except RegistryError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS('Legacy registrations verified/imported; grants unchanged'))
