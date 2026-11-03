"""Small, space-scoped participant previews; viewing a campaign is not participation."""
from django.db.models import Q


def activity_participation(runs, space_id):
    from Activity.models import NumberDuel, UserActivityProgress
    from User.models import User, UserResourceInventory

    groups = {run.pk: set() for run in runs if run.space_id == space_id}
    if not groups:
        return {}
    rows = UserActivityProgress.objects.filter(space_activity_id__in=groups).filter(
        Q(events__isnull=False) | Q(earned_points__gt=0) | Q(contributed_points__gt=0) |
        Q(bgm_participation__isnull=False)
    ).order_by().values_list('space_activity_id', 'user_id').distinct()
    for run_id, user_id in rows:
        groups[run_id].add(user_id)
    games = [run.pk for run in runs if run.pk in groups and run.campaign.config.get('mode') == 'number_duel']
    if games:
        for run_id, host_id, guest_id in NumberDuel.objects.filter(run_id__in=games).values_list('run_id', 'host_id', 'guest_id'):
            groups[run_id].update(user_id for user_id in (host_id, guest_id) if user_id)
    for run in runs:
        if run.pk in groups and run.campaign.config.get('theme') == 'permanent-vip':
            groups[run.pk].update(UserResourceInventory.objects.filter(user__space_id=space_id, reward_id='vip.permanent').values_list('user_id', flat=True))
    ids = set().union(*groups.values())
    members = User.objects.filter(pk__in=ids, space_id=space_id, is_deleted=False).only('id', 'name', 'avatar_uri', 'avatar_type').in_bulk()
    result = {}
    for run_id, candidates in groups.items():
        visible = sorted(candidates.intersection(members))
        result[run_id] = dict(count=len(visible), users=[members[key].dictify('user_id', 'name', 'avatar_uri') for key in visible[:3]])
    return result
