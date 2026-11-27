"""Campaign and room identity shared by the permanent tabletop activities."""
import re
import secrets

from Activity.models import SpaceActivity
from Activity.presentation import presentation


FOUR_PLAYER_MODES = ('shuangkou', 'mahjong')
TABLE_MODES = ('landlord', *FOUR_PLAYER_MODES)
BETWEEN_ROUNDS = ('waiting', 'finished')
ROOM_WAIT = 30 * 60
ROOM_CHARACTERS = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'


def eligible(user, *, allow_verified_contact=False):
    if user.is_deleted or user.is_imported_placeholder:
        return False
    return user.verified or bool(allow_verified_contact and (user.email_verified_at or user.phone_verified_at))


def forfeit_scores(positions, quitter, opponents, stake=1):
    """The quitter pays each opponent. An uninvolved teammate keeps their score."""
    return {position: -stake * len(opponents) if position == quitter
            else stake if position in opponents else 0 for position in positions}


def activity_run(key, user, modes, *, lock=False):
    rows = SpaceActivity.objects.select_for_update() if lock else SpaceActivity.objects
    run = rows.filter(space_id=user.space_id, campaign__key=key, campaign__enabled=True).select_related('campaign').first()
    return run if run and run.campaign.config.get('mode') in modes and run.is_active() else None


def room_code(value):
    if isinstance(value, str):
        value = value.strip().upper()
        if re.fullmatch(f'[{ROOM_CHARACTERS}]{{8}}', value):
            return value
    return None


def new_room(model, **fields):
    while True:
        code = ''.join(secrets.choice(ROOM_CHARACTERS) for _ in range(8))
        if not model.objects.filter(code=code).exists():
            return model.objects.create(code=code, **fields)


def public_user(user):
    return user.dictify('user_id', 'name', 'avatar_uri')


def activity_payload(campaign, user, run):
    return dict(
        key=campaign.key, title=campaign.title, title_en=campaign.title_en, summary=campaign.summary,
        summary_en=campaign.summary_en, theme=campaign.config['mode'], starts_at=run.claimed_at.timestamp(),
        ends_at=run.ends_at.timestamp() if run.ends_at else None, active=run.is_active(),
        assignment_mode=campaign.assignment_mode, duration_seconds=campaign.duration_seconds,
        presentation=presentation(campaign.config), newly_claimed=not run.user_views.filter(user=user).exists(),
        verified=eligible(user), today_earned=False, claimable_points=0, available_points=0, contributed_points=0,
        personal_event_count=0, personal_event_target=0, personal_reward_claimable=False, personal_reward=None,
        space_reward_claimable=None, official_user=None, space_total=0, target=0, milestones=[], awakenings=[],
    )
