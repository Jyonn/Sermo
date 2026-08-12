from datetime import timedelta

from django.utils import timezone

from Square.models import (
    Statement,
    StatementComment,
    StatementCommentLike,
    StatementLike,
    frequency_limits_for_user,
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
            text=True,
            image=True,
            audio=unlimited or level >= 6,
            audio_level=6,
            video=unlimited or level >= 8,
            video_level=8,
        ),
    )
