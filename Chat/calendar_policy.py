"""Account-wide calendar visibility, independently enforced for each viewer."""
from copy import deepcopy
from django.db import transaction
from django.utils import timezone
from Chat.models import ResearchCalendarPreference, ResearchProject
from Friendship.models import Friendship, FriendshipStatusChoice

RANK = {'hidden': 0, 'busy': 1, 'detail': 2}
TIERS = ('strangers', 'friends')


def defaults(mode='busy'):
    return {tier: {'default': mode, 'overrides': {}} for tier in TIERS}


def modes(preference):
    if preference is None:
        return defaults()
    value = preference.overrides
    if value.get('version') == 2:
        return deepcopy(value['audiences'])
    legacy = {'default': 'busy' if preference.default_busy else 'hidden', 'overrides': value}
    return {tier: deepcopy(legacy) for tier in TIERS}


def initialize(user, subject):
    pref = ResearchCalendarPreference.objects.filter(user=user).first()
    audiences = modes(pref) if pref and pref.binding_subject == subject else defaults()
    ResearchCalendarPreference.objects.update_or_create(user=user, defaults={
        'binding_subject': subject, 'default_busy': audiences['strangers']['default'] == 'busy',
        'overrides': {'version': 2, 'audiences': audiences, 'reviewed': False}})


def review_required(user, subject):
    pref = ResearchCalendarPreference.objects.filter(user=user, binding_subject=subject).first()
    return pref is None or (pref.overrides.get('version') == 2 and not pref.overrides.get('reviewed'))


def validate(audiences, group_ids):
    from User.chronelle_account import BindingFailure
    if not isinstance(audiences, dict) or set(audiences) != set(TIERS):
        raise BindingFailure('invalid_policy', 400)
    for tier in TIERS:
        entry = audiences[tier]
        if not isinstance(entry, dict) or set(entry) != {'default', 'overrides'} or not isinstance(entry['default'], str) or entry['default'] not in RANK or not isinstance(entry['overrides'], dict):
            raise BindingFailure('invalid_policy', 400)
        if any(key not in {str(i) for i in group_ids} or not isinstance(mode, str) or mode not in RANK for key, mode in entry['overrides'].items()):
            raise BindingFailure('invalid_policy', 400)
    for key in [None, *map(str, group_ids)]:
        if RANK[effective(audiences, 'friends', key)] < RANK[effective(audiences, 'strangers', key)]:
            raise BindingFailure('friend_permissions_too_low', 400)
    return deepcopy(audiences)


def effective(audiences, tier, group_id):
    entry = audiences[tier]
    return entry['overrides'].get(str(group_id), entry['default']) if group_id is not None else entry['default']


def projection(owner, viewer, group_ids):
    from User.models import ChronelleAccount
    from User.chronelle_account import unpack
    pref = ResearchCalendarPreference.objects.filter(user=owner).first()
    account = ChronelleAccount.objects.filter(user=owner).first()
    if account is None:
        return {'detail': [], 'busy': []}
    value = unpack(account)
    subject = value.get('binding', {}).get('subject')
    if value.get('protocol') != 'oauth-v1' or not subject or (pref and pref.binding_subject != subject):
        return {'detail': [], 'busy': []}
    tier = 'strangers'
    if viewer and viewer.pk != owner.pk and viewer.space_id == owner.space_id:
        low, high = sorted([viewer.pk, owner.pk])
        if Friendship.objects.filter(space_id=owner.space_id, user_low_id=low, user_high_id=high, status=FriendshipStatusChoice.ACCEPTED).exists():
            tier = 'friends'
    audiences = modes(pref)
    result = {'detail': [], 'busy': []}
    for group_id in group_ids:
        mode = effective(audiences, tier, group_id)
        if mode in result:
            result[mode].append(group_id)
    return result


def save(user, subject, audiences, group_ids, base):
    from User.chronelle_account import BindingFailure, credentials, revocation_credentials
    from Chat.research_calendar import revoke_displays
    audiences = validate(audiences, group_ids)
    with transaction.atomic():
        # Use the same lock order as OAuth claim: account, then preferences/projects.
        from User.models import ChronelleAccount
        ChronelleAccount.objects.select_for_update().get(user=user)
        if credentials(user, base)['binding']['subject'] != subject:
            raise BindingFailure('binding_superseded')
        pref = ResearchCalendarPreference.objects.select_for_update().filter(user=user).first()
        before = modes(pref) if pref is None or pref.binding_subject == subject else defaults('hidden')
        keys = {None, *map(str, group_ids)}
        for tier in TIERS:
            keys.update(before[tier]['overrides'])
        reduced = any(RANK[effective(audiences, tier, key)] < RANK[effective(before, tier, key)] for tier in TIERS for key in keys)
        revoked = False
        owned_projects = ResearchProject.objects.filter(chat__chat_members__user=user).values('pk')
        projects = ResearchProject.objects.select_for_update().filter(pk__in=owned_projects).order_by('pk') if reduced else []
        for project in projects:
            own = [item for item in project.calendar_displays if item['owner'] == user.pk and item['viewer'] != user.pk and item['until'] > timezone.now().timestamp()]
            if not own:
                continue
            others = [item for item in project.calendar_displays if item not in own]
            project.calendar_displays = own
            if not revoked:
                revoke_displays(project, base, {user.pk: revocation_credentials(user, base)})
                revoked = True
            project.calendar_displays = others
            project.save(update_fields=['calendar_displays'])
        ResearchCalendarPreference.objects.update_or_create(user=user, defaults={
            'binding_subject': subject, 'default_busy': audiences['strangers']['default'] == 'busy',
            'overrides': {'version': 2, 'audiences': audiences, 'reviewed': True}})
    return {'status': 'saved', 'rebind_required': revoked}


def revoke_friend_displays(left, right):
    """A removed friend must not retain a stronger, still-live display grant."""
    from User.chronelle_account import revocation_credentials
    from Chat.research_calendar import revoke_displays
    users = {left.pk: left, right.pk: right}
    stronger = set()
    for owner in users.values():
        policy = modes(ResearchCalendarPreference.objects.filter(user=owner).first())
        keys = {None, *policy['friends']['overrides'], *policy['strangers']['overrides']}
        if any(RANK[effective(policy, 'friends', key)] > RANK[effective(policy, 'strangers', key)] for key in keys):
            stronger.add(owner.pk)
    if not stronger:
        return
    with transaction.atomic():
        # Keep the same account-before-project lock order as permission updates.
        from User.models import ChronelleAccount
        list(ChronelleAccount.objects.select_for_update().filter(user_id__in=stronger).order_by('user_id'))
        revoked = set()
        related_projects = ResearchProject.objects.filter(chat__chat_members__user_id__in=stronger).values('pk')
        for project in ResearchProject.objects.select_for_update().filter(pk__in=related_projects).order_by('pk'):
            affected = [item for item in project.calendar_displays if item['owner'] in stronger and item['viewer'] in users and item['viewer'] != item['owner'] and item['until'] > timezone.now().timestamp()]
            if not affected:
                continue
            others = [item for item in project.calendar_displays if item not in affected]
            for item in affected:
                base = item.get('base', 'https://chronelle.jyonn.space')
                key = (item['owner'], base)
                if key in revoked:
                    continue
                project.calendar_displays = [item]
                revoke_displays(project, base, {item['owner']: revocation_credentials(users[item['owner']], base)})
                revoked.add(key)
            project.calendar_displays = others
            project.save(update_fields=['calendar_displays'])
