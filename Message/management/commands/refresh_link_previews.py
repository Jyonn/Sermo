from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from Message.models import LinkPreview, LinkPreviewStatusChoice


class Command(BaseCommand):
    help = 'Synchronously refresh cached link previews and print parsing diagnostics.'

    def add_arguments(self, parser):
        parser.add_argument(
            'targets',
            nargs='*',
            help='URLs or text containing a URL. Existing cache entries are reused unless --force is set.',
        )
        parser.add_argument('--failed', action='store_true', help='Include cached previews whose last fetch failed.')
        parser.add_argument('--pending', action='store_true', help='Include cached previews waiting to be fetched.')
        parser.add_argument('--expired', action='store_true', help='Include ready or failed previews whose TTL expired.')
        parser.add_argument('--all', action='store_true', help='Include every cached preview. Requires --force.')
        parser.add_argument('--force', action='store_true', help='Ignore cache freshness and fetch selected previews now.')
        parser.add_argument('--limit', type=int, default=100, help='Maximum selected database rows; 0 means no limit.')

    def handle(self, *args, **options):
        targets = options['targets']
        force = options['force']
        selectors_used = any(options[name] for name in ('failed', 'pending', 'expired', 'all'))
        if not targets and not selectors_used:
            raise CommandError('Provide at least one URL or use --failed, --pending, --expired, or --all.')
        if options['all'] and not force:
            raise CommandError('--all requires --force to avoid refreshing the entire cache accidentally.')
        if options['limit'] < 0:
            raise CommandError('--limit must be 0 or a positive integer.')

        previews = []
        seen_ids = set()
        for target in targets:
            try:
                url = LinkPreview.extract_first_url(target)
            except ValueError as err:
                raise CommandError(f'Invalid or unsafe URL in {target!r}: {err}') from err
            if not url:
                raise CommandError(f'No HTTP(S) URL found in {target!r}.')
            preview, _ = LinkPreview.objects.get_or_create(
                url_hash=LinkPreview.hash_url(url),
                defaults={'url': url, 'status': LinkPreviewStatusChoice.PENDING},
            )
            self._append_unique(previews, seen_ids, preview)

        queryset = LinkPreview.objects.order_by('id')
        if options['all']:
            selected = queryset
        else:
            selected_ids = set()
            if options['failed']:
                selected_ids.update(
                    queryset.filter(status=LinkPreviewStatusChoice.FAILED).values_list('id', flat=True),
                )
            if options['pending']:
                selected_ids.update(
                    queryset.filter(status=LinkPreviewStatusChoice.PENDING).values_list('id', flat=True),
                )
            if options['expired']:
                now = timezone.now()
                for preview in queryset.exclude(status=LinkPreviewStatusChoice.PENDING):
                    if LinkPreview._is_expired(preview, now=now):
                        selected_ids.add(preview.id)
            selected = queryset.filter(id__in=selected_ids)

        limit = options['limit']
        if limit:
            selected = selected[:limit]
        for preview in selected:
            self._append_unique(previews, seen_ids, preview)

        succeeded = failed = skipped = 0
        for index, preview in enumerate(previews, start=1):
            if not force and not self._needs_refresh(preview):
                skipped += 1
                self.stdout.write(f'[{index}/{len(previews)}] SKIP   {preview.url} (cache is fresh)')
                continue

            self.stdout.write(f'[{index}/{len(previews)}] FETCH  {preview.url}')
            LinkPreview.fetch_and_update(preview.id, force=True)
            preview.refresh_from_db()
            if preview.status == LinkPreviewStatusChoice.READY:
                succeeded += 1
                self.stdout.write(self.style.SUCCESS(f'             READY  {preview.title or preview.site_name}'))
                if preview.description:
                    self.stdout.write(f'             DESC   {preview.description[:160]}')
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f'             FAILED {preview.error or "unknown error"}'))

        self.stdout.write(
            self.style.SUCCESS(
                f'Finished: {succeeded} ready, {failed} failed, {skipped} skipped, {len(previews)} selected.',
            ),
        )
        if failed:
            raise CommandError(f'{failed} link preview(s) failed; see errors above.')

    @staticmethod
    def _append_unique(previews, seen_ids, preview):
        if preview.id in seen_ids:
            return
        seen_ids.add(preview.id)
        previews.append(preview)

    @staticmethod
    def _needs_refresh(preview):
        return preview.status == LinkPreviewStatusChoice.PENDING or LinkPreview._is_expired(preview)
