"""Private, non-transferable birthday celebrations, using Frienden's Shanghai day."""
import calendar
import datetime
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Error, Code
from User.models import User, BirthdayLetter

ZONE = ZoneInfo('Asia/Shanghai')
CHANGE_INTERVAL = datetime.timedelta(days=365)


@Error.register
class BirthdayErrors:
    INVALID_DATE = Error(message=_('Choose a valid birthday month and day'), code=Code.BadRequest)
    LOCKED = Error(message=_('Birthday can only be changed once every 365 days'), code=Code.Forbidden)
    NOT_SET = Error(message=_('This member has not set a birthday'), code=Code.BadRequest)
    INVALID_LETTER = Error(message=_('Enter a title up to 80 characters and a letter up to 3000 characters'), code=Code.BadRequest)
    UNAVAILABLE = Error(message=_('This birthday letter is not available'), code=Code.NotFound)
    FORBIDDEN = Error(message=_('You cannot manage another space member'), code=Code.Forbidden)
    STALE = Error(message=_('The birthday occasion changed. Reload before saving'), code=Code.Conflict)


def valid_birthday(month, day):
    if type(month) is not int or type(day) is not int:
        raise BirthdayErrors.INVALID_DATE
    try:
        datetime.date(2000, month, day)
    except (ValueError, TypeError):
        raise BirthdayErrors.INVALID_DATE
    return month, day


def observed_date(user, year):
    if not user.birthday_month or not user.birthday_day:
        return None
    day = min(user.birthday_day, calendar.monthrange(year, user.birthday_month)[1])
    return datetime.date(year, user.birthday_month, day)


def next_occasion(user, now=None):
    today = (now or timezone.now()).astimezone(ZONE).date()
    birthday = observed_date(user, today.year)
    return observed_date(user, today.year + int(birthday < today)) if birthday else None


def birthday_status(user, now=None):
    now = now or timezone.now()
    today = now.astimezone(ZONE).date()
    active = observed_date(user, today.year) == today
    available = user.birthday_set_at + CHANGE_INTERVAL if user.birthday_set_at else None
    letters = BirthdayLetter.objects.filter(user=user, birthday_year=today.year, occasion_date__lte=today).order_by('source', 'id') if active else []
    end = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(), ZONE) if active else None
    return dict(server_now=now.timestamp(), month=user.birthday_month, day=user.birthday_day,
                can_change=available is None or now >= available,
                available_at=available.timestamp() if available else None,
                active=active, celebration_id=f'{user.id}:{today.year}' if active else None,
                ends_at=end.timestamp() if end else None,
                auto_open=active and user.birthday_welcomed_year != today.year,
                letters=[row.envelope() for row in letters])


@transaction.atomic
def set_birthday(user, month, day, now=None):
    now = now or timezone.now()
    valid_birthday(month, day)
    locked = User.objects.select_for_update().get(pk=user.pk)
    if locked.birthday_set_at and now < locked.birthday_set_at + CHANGE_INTERVAL:
        raise BirthdayErrors.LOCKED
    locked.birthday_month, locked.birthday_day = month, day
    locked.birthday_set_at = now
    locked.save(update_fields=['birthday_month', 'birthday_day', 'birthday_set_at'])
    return birthday_status(locked, now)


def admin_letter_payload(user, source, now=None):
    occasion = next_occasion(user, now)
    row = BirthdayLetter.objects.filter(user=user, birthday_year=occasion.year, source=source).first() if occasion else None
    return dict(birthday_month=user.birthday_month, birthday_day=user.birthday_day,
                birthday_year=occasion.year if occasion else None, occasion=occasion.isoformat() if occasion else None,
                letter=dict(title=row.title, body=row.body, opened_at=row.opened_at.timestamp() if row.opened_at else None) if row else None)


@transaction.atomic
def save_letter(user, source, sender_name, actor, data, now=None):
    user = User.objects.select_for_update().get(pk=user.pk)
    occasion = next_occasion(user, now)
    if occasion is None:
        raise BirthdayErrors.NOT_SET
    title, body = data.get('title'), data.get('body')
    if not isinstance(title, str) or not isinstance(body, str) or not 1 <= len(title.strip()) <= 80 or not 1 <= len(body.strip()) <= 3000:
        raise BirthdayErrors.INVALID_LETTER
    if type(data.get('birthday_year')) is not int or data['birthday_year'] != occasion.year:
        raise BirthdayErrors.STALE
    previous = BirthdayLetter.objects.filter(user=user, birthday_year=occasion.year, source=source).first()
    if previous and previous.title == title.strip() and previous.body == body.strip() and previous.sender_name == sender_name[:100] and previous.occasion_date == occasion:
        return admin_letter_payload(user, source, now)
    row, _created = BirthdayLetter.objects.update_or_create(
        user=user, birthday_year=occasion.year, source=source,
        defaults=dict(title=title.strip(), body=body.strip(), sender_name=sender_name[:100], written_by=actor[:254], occasion_date=occasion),
    )
    # An edited, already-read letter becomes an unread updated version.
    if row.opened_at:
        row.opened_at = None
        row.save(update_fields=['opened_at'])
    from PlatformAdmin.models import PlatformAuditLog
    PlatformAuditLog.objects.create(action='birthday.letter.saved', target_type='user', target_id=user.id,
                                   summary='Birthday letter saved', metadata={'source': source, 'birthday_year': occasion.year, 'actor': actor})
    return admin_letter_payload(user, source, now)
