import json
from django.db import transaction
from django.utils import timezone
from django.views import View
from utils import auth
from User.models import User, BirthdayLetter
from User.birthday import BirthdayErrors, birthday_status, set_birthday, admin_letter_payload, save_letter, ZONE


def body(request):
    try:
        value = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        raise BirthdayErrors.INVALID_LETTER
    if not isinstance(value, dict):
        raise BirthdayErrors.INVALID_LETTER
    return value


class BirthdayView(View):
    @auth.require_user
    def get(self, request):
        return birthday_status(request.user)

    @auth.require_user
    def post(self, request):
        data = body(request)
        return set_birthday(request.user, data.get('month'), data.get('day'))


class BirthdaySeenView(View):
    @auth.require_user
    def post(self, request):
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            now = timezone.now()
            if birthday_status(user, now)['active']:
                user.birthday_welcomed_year = now.astimezone(ZONE).year
                user.save(update_fields=['birthday_welcomed_year'])
            return birthday_status(user, now)


class BirthdayLetterOpenView(View):
    @auth.require_user
    def post(self, request, letter_id):
        with transaction.atomic():
            try:
                row = BirthdayLetter.objects.select_for_update().get(pk=letter_id, user=request.user)
            except BirthdayLetter.DoesNotExist:
                raise BirthdayErrors.UNAVAILABLE
            now = timezone.now()
            if row.occasion_date > now.astimezone(ZONE).date():
                raise BirthdayErrors.UNAVAILABLE
            if row.opened_at is None:
                row.opened_at = now
                row.save(update_fields=['opened_at'])
            return dict(**row.envelope(), body=row.body)


class BirthdayLetterArchiveView(View):
    @auth.require_user
    def get(self, request):
        today = timezone.now().astimezone(ZONE).date()
        return [row.envelope() for row in BirthdayLetter.objects.filter(user=request.user, occasion_date__lte=today).order_by('-occasion_date', '-id')[:50]]


class SpaceBirthdayLetterView(View):
    @auth.require_space
    def get(self, request, user_id):
        user = User.index(user_id)
        if user.space_id != request.space.id:
            raise BirthdayErrors.FORBIDDEN
        return admin_letter_payload(user, 'space')

    @auth.require_space
    def post(self, request, user_id):
        user = User.index(user_id)
        if user.space_id != request.space.id:
            raise BirthdayErrors.FORBIDDEN
        return save_letter(user, 'space', request.space.name, request.space.email, body(request))


class PlatformBirthdayLetterView(View):
    @auth.require_platform_admin
    def get(self, request, user_id):
        return admin_letter_payload(User.index(user_id), 'platform')

    @auth.require_platform_admin
    def post(self, request, user_id):
        return save_letter(User.index(user_id), 'platform', 'Frienden', request.platform_admin_email, body(request))
