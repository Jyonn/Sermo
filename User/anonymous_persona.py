import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from smartdjango import Validator

from Space.models import Space
from User.models import AnonymousPersona
from User.validators import UserErrors


ADJECTIVES = (
    ('安静', 'Quiet'), ('困倦', 'Sleepy'), ('温柔', 'Gentle'), ('好奇', 'Curious'),
    ('慵懒', 'Lazy'), ('勇敢', 'Brave'), ('害羞', 'Shy'), ('快乐', 'Happy'),
    ('淘气', 'Playful'), ('悠闲', 'Leisurely'), ('机灵', 'Clever'), ('热心', 'Kind'),
    ('迷糊', 'Dreamy'), ('活泼', 'Lively'), ('谨慎', 'Careful'), ('倔强', 'Stubborn'),
)
NOUNS = (
    ('海獭', 'Otter'), ('松鼠', 'Squirrel'), ('海鸥', 'Gull'), ('企鹅', 'Penguin'),
    ('狐狸', 'Fox'), ('青蛙', 'Frog'), ('河狸', 'Beaver'), ('黑猫', 'Cat'),
    ('刺猬', 'Hedgehog'), ('水母', 'Jellyfish'), ('信鸽', 'Pigeon'), ('萤火虫', 'Firefly'),
    ('兔子', 'Rabbit'), ('小鹿', 'Deer'), ('树懒', 'Sloth'), ('熊猫', 'Panda'),
)
LIFETIME = timedelta(minutes=30)
persona_token = Validator('anonymous_token').to(str).bool(lambda token: len(token) <= 40).default('')


def hue_for(slot):
    return (slot * 137) % 360


def public_persona(slot):
    adjective, noun = divmod(slot, len(NOUNS))
    zh = ADJECTIVES[adjective][0] + NOUNS[noun][0]
    return dict(id=zh, nickname=zh, nickname_en=f'{ADJECTIVES[adjective][1]} {NOUNS[noun][1]}',
                hue=hue_for(slot))


@transaction.atomic
def issue_persona(user, scope, target_id):
    # The space row serializes reservations across workers without exposing any account identifiers.
    Space.objects.select_for_update().only('id').get(pk=user.space_id)
    now = timezone.now()
    candidates = AnonymousPersona.objects.filter(scope=scope, target_id=target_id, user__space_id=user.space_id)
    previous = candidates.filter(user=user).first()
    active_slots = set(candidates.filter(expires_at__gt=now).values_list('slot', flat=True))
    available = set(range(len(ADJECTIVES) * len(NOUNS))) - active_slots
    if previous:
        available.discard(previous.slot)
    if not available:
        raise UserErrors.ANONYMOUS_PERSONA_UNAVAILABLE
    active_hues = [hue_for(slot) for slot in active_slots]
    distinct_hues = [
        slot for slot in sorted(available)
        if all(min(abs(hue_for(slot) - hue), 360 - abs(hue_for(slot) - hue)) >= 26 for hue in active_hues)
    ]
    slot = secrets.choice(distinct_hues or sorted(available))
    row, _ = AnonymousPersona.objects.update_or_create(
        user=user, scope=scope, target_id=target_id,
        defaults=dict(slot=slot, token=secrets.token_urlsafe(24), expires_at=now + LIFETIME, active=True),
    )
    return dict(persona=public_persona(slot), token=row.token, expires_at=row.expires_at.timestamp())


def resolve_persona(user, scope, target_id, token):
    if not token:
        # A send from an older client still receives a fresh, single-use public identity.
        issued = issue_persona(user, scope, target_id)
        discard_persona(user, issued['token'])
        return issued['persona']
    row = AnonymousPersona.objects.filter(
        user=user, scope=scope, target_id=target_id, token=token, active=True, expires_at__gt=timezone.now(),
    ).only('slot').first()
    if row is None:
        raise UserErrors.ANONYMOUS_PERSONA_EXPIRED
    return public_persona(row.slot)


def discard_persona(user, token):
    AnonymousPersona.objects.filter(user=user, token=token).update(active=False)
