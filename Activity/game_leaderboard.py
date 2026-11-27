"""Space-scoped rankings from the games' settled results."""
from datetime import datetime
from fractions import Fraction
from zoneinfo import ZoneInfo

from django.db.models import Count, F, Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.models import FourPlayerResult, LandlordRoundResult, NumberDuelReward, SpaceActivity
from Activity.table_activity import TABLE_MODES, public_user
from User.models import User


@Error.register
class GameLeaderboardErrors:
    UNAVAILABLE = Error(_('This game leaderboard is unavailable in your space'), code=Code.Forbidden)
    INVALID = Error(_('Choose an available year and ranking metric'), code=Code.BadRequest)


ZONE = ZoneInfo('Asia/Shanghai')


def game_leaderboard(key, user, year=None, metric='points'):
    run = SpaceActivity.objects.select_related('campaign').filter(
        space_id=user.space_id, campaign__key=key, campaign__enabled=True,
        campaign__config__mode__in=(*TABLE_MODES, 'number_duel'),
    ).first()
    if run is None:
        raise GameLeaderboardErrors.UNAVAILABLE
    return annual_rankings(run, user, year, metric)


def _results(run, start, end):
    mode = run.campaign.config.get('mode')
    if mode == 'number_duel':
        return NumberDuelReward.objects.filter(
            duel__run=run, counted=True, duel__finished_at__gte=start, duel__finished_at__lt=end,
        ), 'points', Q(duel__winner_id=F('user_id'))
    model = LandlordRoundResult if mode == 'landlord' else FourPlayerResult
    return model.objects.filter(table__run=run, finished_at__gte=start, finished_at__lt=end), 'score', Q(won=True)


def annual_rankings(run, user, year=None, metric='points'):
    now_year = timezone.now().astimezone(ZONE).year
    first_year = run.claimed_at.astimezone(ZONE).year
    try:
        year = int(year) if year else now_year
    except (TypeError, ValueError):
        raise GameLeaderboardErrors.INVALID
    if not first_year <= year <= now_year or metric not in ('points', 'win_rate'):
        raise GameLeaderboardErrors.INVALID

    results, points, win = _results(run, datetime(year, 1, 1, tzinfo=ZONE), datetime(year + 1, 1, 1, tzinfo=ZONE))
    rows = list(results.filter(user__space_id=run.space_id, user__is_deleted=False)
                .values('user_id').annotate(games=Count('pk'), wins=Count('pk', filter=win), points=Sum(points)))
    for row in rows:
        row['rate'] = Fraction(row['wins'], row['games'])
    rows.sort(key=lambda row: (-row['points'], row['user_id']) if metric == 'points'
              else (-row['rate'], -row['games'], row['user_id']))
    members = User.objects.only('id', 'name', 'avatar_type', 'avatar_uri').in_bulk([row['user_id'] for row in rows[:50]])
    top, mine, place, previous = [], None, 0, None
    for position, row in enumerate(rows, 1):
        score = row['points'] if metric == 'points' else row['rate']
        if score != previous:
            previous, place = score, position
        result = dict(rank=place, wins=row['wins'], games=row['games'], points=row['points'], win_rate=float(row['rate']))
        if row['user_id'] == user.pk:
            mine = result
        if member := members.get(row['user_id']):
            top.append(dict(**result, member=public_user(member)))
    return dict(year=year, years=list(range(now_year, first_year - 1, -1)), metric=metric, rows=top, mine=mine)
