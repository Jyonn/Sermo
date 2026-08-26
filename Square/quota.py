from datetime import timedelta

from django.utils import timezone

from Square.models import (
    Statement,
    StatementComment,
    StatementCommentLike,
    StatementLike,
    frequency_limits_for_user,
    anonymous_weekly_limit_for_user,
)


def quota_for_user(user):
    level = user.effective_growth_level()
    daily_limit, weekly_limit = frequency_limits_for_user(user)
    now = timezone.now()
    day_start = now - timedelta(days=1)
    week_start = now - timedelta(days=7)
    statements = Statement.objects.filter(space=user.space, user=user, is_deleted=False)
    comments = StatementComment.objects.filter(statement__space=user.space, user=user, is_deleted=False)
    unlimited = bool(user.is_official)
    text_allowed = user.has_capability('square.statement.publish.text')
    image_allowed = user.has_capability('square.statement.publish.image')
    audio_allowed = user.has_capability('square.statement.publish.audio')
    video_allowed = user.has_capability('square.statement.publish.video')

    return dict(
        level=level,
        vip=bool(user.is_permanent_vip),
        verified=bool(user.verified),
        unlimited=unlimited,
        statements=dict(
            daily_used=statements.filter(created_at__gte=day_start).count(),
            daily_limit=None if unlimited else daily_limit,
            weekly_used=statements.filter(created_at__gte=week_start).count(),
            weekly_limit=None if unlimited else weekly_limit,
            anonymous_weekly_used=statements.filter(is_anonymous=True, created_at__gte=week_start).count(),
            anonymous_weekly_limit=None if unlimited else anonymous_weekly_limit_for_user(user),
            anonymous_available=bool(user.space.square_explore_enabled),
        ),
        comments=dict(
            daily_used=comments.filter(created_at__gte=day_start).count(),
            daily_limit=None if unlimited else daily_limit * 5,
            weekly_used=comments.filter(created_at__gte=week_start).count(),
            weekly_limit=None if unlimited else weekly_limit * 5,
        ),
        likes=dict(
            daily_used=(
                StatementLike.objects.filter(user=user, created_at__gte=day_start).count()
                + StatementCommentLike.objects.filter(user=user, created_at__gte=day_start).count()
            ),
            unlimited=True,
        ),
        media=dict(
            text=unlimited or text_allowed,
            image=unlimited or image_allowed,
            audio=unlimited or audio_allowed,
            audio_level=user.capability_required_level('square.statement.publish.audio', fallback=6),
            video=unlimited or video_allowed,
            video_level=user.capability_required_level('square.statement.publish.video', fallback=8),
        ),
    )
