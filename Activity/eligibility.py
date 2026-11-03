"""One space feature policy for activity assignment and admin reporting."""
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error


@Error.register
class ActivityEligibilityErrors:
    UNAVAILABLE = Error(_('Space features do not meet this activity’s requirements'), code=Code.Forbidden)


def missing_activity_requirements(campaign, space):
    square = space.verification_tier != 'email' and space.group_square_enabled
    capabilities = {
        'square': square,
        'free_post': square and space.square_free_post_enabled,
        'chat': space.chat_enabled,
        'submission': square and space.submission_enabled,
    }
    capabilities['publish'] = capabilities['free_post'] or capabilities['submission']
    required = set(campaign.config.get('requirements', []))
    mode = campaign.config.get('mode')
    if campaign.event_key.startswith('square.') and mode not in {'number_duel', 'annual_explorer'}:
        required.add('square')
    if campaign.event_key == 'square.statement.publish' and mode not in {'number_duel', 'annual_explorer'}:
        required.add('free_post')
    if campaign.event_key.startswith('chat.') or mode in {'starry_night_streak', 'bgm_collection'}:
        required.add('chat')
    if mode == 'annual_explorer':
        required.add('publish')
    # BGM progression includes publishing a Square statement as well as chat entrances.
    if mode == 'bgm_collection':
        required.add('free_post')
    return sorted(key for key in required if not capabilities.get(key, False))
