from collections import Counter, defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from Message.models import Message, MessageTypeChoice
from User.models import UserEmojiUsage, extract_emoji_counts


class Command(BaseCommand):
    help = 'Rebuild per-user emoji usage from existing text messages.'

    def handle(self, *args, **options):
        totals = defaultdict(Counter)
        latest = {}
        messages = Message.objects.filter(
            type=MessageTypeChoice.TEXT,
            is_deleted=False,
        ).only('user_id', 'content', 'created_at').iterator(chunk_size=1000)
        for message in messages:
            counts = extract_emoji_counts(message.content)
            if not counts:
                continue
            totals[message.user_id].update(counts)
            for emoji in counts:
                latest[(message.user_id, emoji)] = max(
                    latest.get((message.user_id, emoji), message.created_at),
                    message.created_at,
                )

        now = timezone.now()
        rows = [
            UserEmojiUsage(
                user_id=user_id,
                emoji=emoji,
                use_count=count,
                last_used_at=latest.get((user_id, emoji), now),
            )
            for user_id, counts in totals.items()
            for emoji, count in counts.items()
        ]
        with transaction.atomic():
            UserEmojiUsage.objects.all().delete()
            UserEmojiUsage.objects.bulk_create(rows, batch_size=1000)
        self.stdout.write(self.style.SUCCESS(f'Rebuilt {len(rows)} emoji usage rows.'))
