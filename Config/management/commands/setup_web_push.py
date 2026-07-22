import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.core.management.base import BaseCommand

from Config.models import Config, CI


def encode_urlsafe(value: bytes):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


class Command(BaseCommand):
    help = 'Generate and save the VAPID key pair used by browser push notifications.'

    def add_arguments(self, parser):
        parser.add_argument('--subject', default='mailto:admin@sermo.jyonn.space')
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **options):
        existing = Config.get_value_by_key(CI.WEB_PUSH_VAPID_PRIVATE_KEY)
        if existing and not options['force']:
            self.stdout.write(self.style.WARNING('Web Push is already configured. Use --force to replace its keys.'))
            return

        private_key = ec.generate_private_key(ec.SECP256R1())
        private_value = private_key.private_numbers().private_value.to_bytes(32, 'big')
        public_value = private_key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)

        Config.update_value(CI.WEB_PUSH_VAPID_PRIVATE_KEY, encode_urlsafe(private_value))
        Config.update_value(CI.WEB_PUSH_VAPID_PUBLIC_KEY, encode_urlsafe(public_value))
        Config.update_value(CI.WEB_PUSH_VAPID_SUBJECT, options['subject'])
        self.stdout.write(self.style.SUCCESS('Web Push VAPID keys are configured.'))
