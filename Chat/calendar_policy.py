"""Account-wide calendar visibility, independently enforced for each viewer."""
from copy import deepcopy
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from Chat.models import ResearchCalendarPreference, ResearchProject
from Friendship.models import Friendship, FriendshipStatusChoice

RANK = {'hidden': 0, 'busy': 1, 'detail': 2}
TIERS = ('strangers', 'friends')
DETAIL_FIELDS = ('location', 'description')


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


def detail_fields(preference):
    saved = preference.overrides.get('detail_fields', {}) if preference and preference.overrides.get('version') == 2 else {}
    return {key: saved.get(key, True) for key in DETAIL_FIELDS}


def friend_overrides(preference):
    if preference and preference.overrides.get('version') == 2:
        return deepcopy(preference.overrides.get('friend_overrides', {}))
    return {}


def inherited_friend_policy():
    return {'default': None, 'overrides': {}}


def policy_document(audiences, fields, reviewed, exceptions):
    return {'version': 2, 'audiences': audiences, 'reviewed': reviewed,
            'detail_fields': fields, 'friend_overrides': exceptions}


def accepted_friendships(owner):
    return Friendship.objects.filter(space_id=owner.space_id, status=FriendshipStatusChoice.ACCEPTED).filter(
        Q(user_low_id=owner.pk, user_high__is_deleted=False) | Q(user_high_id=owner.pk, user_low__is_deleted=False))


def friend_relation(owner, friend_id):
    low, high = sorted([owner.pk, friend_id])
    return accepted_friendships(owner).filter(user_low_id=low, user_high_id=high)


def shared_detail_fields(user, subject):
    return detail_fields(ResearchCalendarPreference.objects.filter(user=user, binding_subject=subject).first())


def initialize(user, subject):
    pref = ResearchCalendarPreference.objects.filter(user=user).first()
    if pref and pref.binding_subject != subject:
        pref = None
    audiences = modes(pref)
    ResearchCalendarPreference.objects.update_or_create(user=user, defaults={
        'binding_subject': subject, 'default_busy': audiences['strangers']['default'] == 'busy',
        'overrides': policy_document(audiences, detail_fields(pref), False, friend_overrides(pref))})


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


def friend_mode(audiences, policy, group_id):
    if group_id is not None and str(group_id) in policy['overrides']:
        return policy['overrides'][str(group_id)]
    return policy['default'] or effective(audiences, 'friends', group_id)


def projection(owner, viewer, group_ids, *, preview_tier=None, expected_subject=None):
    from User.models import ChronelleAccount
    from User.chronelle_account import unpack
    pref = ResearchCalendarPreference.objects.filter(user=owner).first()
    account = ChronelleAccount.objects.filter(user=owner).first()
    if account is None:
        return {'detail': [], 'busy': []}
    value = unpack(account)
    subject = value.get('binding', {}).get('subject')
    if (value.get('protocol') != 'oauth-v1' or not subject or (pref and pref.binding_subject != subject)
            or (expected_subject is not None and subject != expected_subject)):
        return {'detail': [], 'busy': []}
    tier = 'strangers'
    audiences = modes(pref)
    exception = None
    if viewer and viewer.pk != owner.pk and viewer.space_id == owner.space_id and friend_relation(owner, viewer.pk).exists():
        tier = 'friends'
        exception = friend_overrides(pref).get(str(viewer.pk))
    if preview_tier in TIERS and viewer and viewer.pk == owner.pk:
        tier = preview_tier
    result = {'detail': [], 'busy': []}
    for group_id in group_ids:
        mode = friend_mode(audiences, exception, group_id) if exception else effective(audiences, tier, group_id)
        if mode in result:
            result[mode].append(group_id)
    return result


def save(user, subject, audiences, group_ids, base, fields=None):
    from User.chronelle_account import BindingFailure, credentials
    audiences = validate(audiences, group_ids)
    if fields is not None and (not isinstance(fields, dict) or set(fields) != set(DETAIL_FIELDS)
                               or any(type(value) is not bool for value in fields.values())):
        raise BindingFailure('invalid_policy', 400)
    with transaction.atomic():
        # Use the same lock order as OAuth claim: account, then preferences/projects.
        from User.models import ChronelleAccount
        ChronelleAccount.objects.select_for_update().get(user=user)
        if credentials(user, base)['binding']['subject'] != subject:
            raise BindingFailure('binding_superseded')
        pref = ResearchCalendarPreference.objects.select_for_update().filter(user=user).first()
        matching = pref if pref and pref.binding_subject == subject else None
        before = modes(matching) if pref is None or matching else defaults('hidden')
        before_fields = detail_fields(matching)
        fields = before_fields if fields is None else fields
        keys = {None, *map(str, group_ids)}
        for tier in TIERS:
            keys.update(before[tier]['overrides'])
        reduced = any(RANK[effective(audiences, tier, key)] < RANK[effective(before, tier, key)] for tier in TIERS for key in keys)
        reduced = reduced or any(before_fields[key] and not fields[key] for key in DETAIL_FIELDS)
        revoked = revoke_owner_displays(user, base) if reduced else False
        ResearchCalendarPreference.objects.update_or_create(user=user, defaults={
            'binding_subject': subject, 'default_busy': audiences['strangers']['default'] == 'busy',
            'overrides': policy_document(audiences, fields, True, friend_overrides(matching))})
    return {'status': 'saved', 'rebind_required': revoked}


def revoke_owner_displays(owner, base, viewer_id=None):
    from User.chronelle_account import revocation_credentials
    from Chat.research_calendar import revoke_displays
    project_ids = ResearchProject.objects.filter(chat__chat_members__user=owner).values('pk')
    projects = list(ResearchProject.objects.select_for_update().filter(pk__in=project_ids).order_by('pk'))
    now = timezone.now().timestamp()
    affected = [(project, item) for project in projects for item in project.calendar_displays
                if item['owner'] == owner.pk and item['viewer'] != owner.pk and item['until'] > now
                and item.get('protocol') == 'oauth-v1' and (viewer_id is None or item['viewer'] == viewer_id)]
    if not affected:
        return False
    revoked_bases = set()
    for project, item in affected:
        origin = item.get('base', base)
        if origin not in revoked_bases:
            stored = project.calendar_displays
            project.calendar_displays = [item]
            revoke_displays(project, origin, {owner.pk: revocation_credentials(owner, origin)})
            project.calendar_displays = stored
            revoked_bases.add(origin)
    # Revocation is account-wide for the affected provider grant.
    for project in projects:
        remaining = [item for item in project.calendar_displays
                     if item['owner'] != owner.pk or item['viewer'] == owner.pk or item.get('base', base) not in revoked_bases]
        if remaining != project.calendar_displays:
            project.calendar_displays = remaining
            project.save(update_fields=['calendar_displays'])
    return True


def save_friend_policy(user, subject, friend_id, policy, group_ids, base):
    from User.chronelle_account import BindingFailure, credentials
    from User.models import ChronelleAccount
    if (not isinstance(policy, dict) or set(policy) != {'default', 'overrides'}
            or (policy['default'] is not None and (not isinstance(policy['default'], str) or policy['default'] not in RANK))
            or not isinstance(policy['overrides'], dict)):
        raise BindingFailure('invalid_policy', 400)
    groups = {str(group) for group in group_ids}
    if any(key not in groups or not isinstance(mode, str) or mode not in RANK for key, mode in policy['overrides'].items()):
        raise BindingFailure('invalid_policy', 400)
    with transaction.atomic():
        ChronelleAccount.objects.select_for_update().get(user=user)
        if credentials(user, base)['binding']['subject'] != subject:
            raise BindingFailure('binding_superseded')
        if not friend_relation(user, friend_id).select_for_update().first():
            raise BindingFailure('friend_unavailable', 404)
        pref = ResearchCalendarPreference.objects.select_for_update().filter(user=user).first()
        if pref and pref.binding_subject != subject:
            raise BindingFailure('binding_superseded')
        audiences = modes(pref)
        exceptions = friend_overrides(pref)
        key = str(friend_id)
        before = exceptions.get(key, inherited_friend_policy())
        if before == policy:
            return {'status': 'saved', 'rebind_required': False}
        reduced = any(RANK[friend_mode(audiences, policy, group)] < RANK[friend_mode(audiences, before, group)]
                      for group in {None, *groups, *before['overrides']})
        revoked = revoke_owner_displays(user, base, friend_id) if reduced else False
        if policy == inherited_friend_policy():
            exceptions.pop(key, None)
        else:
            exceptions[key] = policy
        reviewed = bool(pref and (pref.overrides.get('version') != 2 or pref.overrides.get('reviewed')))
        ResearchCalendarPreference.objects.update_or_create(user=user, defaults={
            'binding_subject': subject, 'default_busy': audiences['strangers']['default'] == 'busy',
            'overrides': policy_document(audiences, detail_fields(pref), reviewed, exceptions)})
    return {'status': 'saved', 'rebind_required': revoked}


def revoke_friend_displays(left, right):
    """Retire both directions of sharing exceptions when a friendship ends."""
    from User.chronelle_origins import BASE
    from User.models import ChronelleAccount
    with transaction.atomic():
        list(ChronelleAccount.objects.select_for_update().filter(user_id__in=[left.pk, right.pk]).order_by('user_id'))
        friend_relation(left, right.pk).select_for_update().first()
        for owner, peer in ((left, right), (right, left)):
            pref = ResearchCalendarPreference.objects.select_for_update().filter(user=owner).first()
            audiences = modes(pref)
            exceptions = friend_overrides(pref)
            exception = exceptions.pop(str(peer.pk), None)
            policy = exception or inherited_friend_policy()
            keys = {None, *audiences['friends']['overrides'], *audiences['strangers']['overrides'], *policy['overrides']}
            if any(RANK[friend_mode(audiences, policy, key)] > RANK[effective(audiences, 'strangers', key)] for key in keys):
                revoke_owner_displays(owner, BASE, peer.pk)
            if exception:
                pref.overrides['friend_overrides'] = exceptions
                pref.save(update_fields=['overrides'])
