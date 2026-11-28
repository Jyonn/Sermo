from Chat import voice
from Message import social_quota
from Message.models import Message, MessageHistoryRecovery, PinnedMessage, WelcomeMessageTemplate
from Square.models import COMMENT_FREQUENCY_MULTIPLIER, anonymous_statement_limit, effective_frequency_limits
from User.growth import EVENT_RULES


def permanent_vip_privileges():
    """Publish standard VIP allowances, without requiring the viewer to have claimed a seat."""
    daily, weekly = effective_frequency_limits(1, True, {})
    return {
        'recall_days': Message.VIP_RECALL_WINDOW.days,
        'pinned_messages': PinnedMessage.VIP_LIMIT,
        'history_recoveries': MessageHistoryRecovery.VIP_LIMIT,
        'welcome_messages': WelcomeMessageTemplate.PRIVILEGED_LIMIT,
        'voice_seconds': voice.WEEK_SECONDS,
        'social_parses': social_quota.WEEKLY_LIMIT,
        'square': {
            'statement_daily': daily,
            'statement_weekly': weekly,
            'anonymous_weekly': anonymous_statement_limit(weekly),
            'comment_daily': daily * COMMENT_FREQUENCY_MULTIPLIER,
            'comment_weekly': weekly * COMMENT_FREQUENCY_MULTIPLIER,
        },
        'growth_points': EVENT_RULES['vip:permanent'].points,
    }
