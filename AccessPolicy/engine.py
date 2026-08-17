from dataclasses import dataclass, field

from AccessPolicy.catalog import ancestors, get_capability
from AccessPolicy.models import PlatformCapabilityPolicy, SpaceCapabilityPolicy
from AccessPolicy.validators import AccessPolicyErrors


ALLOWED_FIELDS = {
    'growth_level', 'has_password', 'verified', 'email_verified', 'phone_verified',
    'dual_verified', 'permanent_vip', 'official', 'chat_enabled', 'square_enabled',
    'square_explore_enabled', 'space_phone_verified', 'space_identity_verified',
    'unverified_group_policy', 'qr_invite',
}
ALLOWED_OPERATORS = {'eq', 'neq', 'gte', 'gt', 'lte', 'lt', 'in', 'not_in', 'exists'}
MAX_DEPTH = 8
MAX_NODES = 64


def validate_expression(expression, depth=0, counter=None):
    if expression in (None, {}):
        return {}
    if not isinstance(expression, dict) or depth > MAX_DEPTH:
        raise AccessPolicyErrors.POLICY_INVALID
    counter = counter if counter is not None else [0]
    counter[0] += 1
    if counter[0] > MAX_NODES:
        raise AccessPolicyErrors.POLICY_INVALID
    if set(expression) == {'all'} or set(expression) == {'any'}:
        key = next(iter(expression))
        values = expression[key]
        if not isinstance(values, list) or not values:
            raise AccessPolicyErrors.POLICY_INVALID
        return {key: [validate_expression(value, depth + 1, counter) for value in values]}
    if set(expression) == {'not'}:
        return {'not': validate_expression(expression['not'], depth + 1, counter)}
    if set(expression) != {'field', 'op', 'value'}:
        raise AccessPolicyErrors.POLICY_INVALID
    field_name = expression['field']
    operator = expression['op']
    if field_name not in ALLOWED_FIELDS or operator not in ALLOWED_OPERATORS:
        raise AccessPolicyErrors.POLICY_INVALID
    value = expression['value']
    if operator in {'in', 'not_in'} and not isinstance(value, list):
        raise AccessPolicyErrors.POLICY_INVALID
    return {'field': field_name, 'op': operator, 'value': value}


def validate_limits(limits):
    if limits in (None, {}):
        return {}
    if not isinstance(limits, dict) or len(limits) > 24:
        raise AccessPolicyErrors.POLICY_INVALID
    normalized = {}
    for key, value in limits.items():
        if not isinstance(key, str) or len(key) > 64 or not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise AccessPolicyErrors.POLICY_INVALID
        normalized[key] = value
    return normalized


def evaluate_expression(expression, context):
    if not expression:
        return True
    if 'all' in expression:
        return all(evaluate_expression(value, context) for value in expression['all'])
    if 'any' in expression:
        return any(evaluate_expression(value, context) for value in expression['any'])
    if 'not' in expression:
        return not evaluate_expression(expression['not'], context)
    actual = context.get(expression['field'])
    expected = expression['value']
    operator = expression['op']
    try:
        if operator == 'eq':
            return actual == expected
        if operator == 'neq':
            return actual != expected
        if operator == 'gte':
            return actual >= expected
        if operator == 'gt':
            return actual > expected
        if operator == 'lte':
            return actual <= expected
        if operator == 'lt':
            return actual < expected
        if operator == 'in':
            return actual in expected
        if operator == 'not_in':
            return actual not in expected
        if operator == 'exists':
            return (actual is not None) is bool(expected)
    except TypeError:
        return False
    return False


def subject_context(user=None, space=None, overrides=None):
    space = space or getattr(user, 'space', None)
    email_verified = bool(getattr(user, 'email_verified_at', None))
    phone_verified = bool(getattr(user, 'phone_verified_at', None))
    values = {
        'growth_level': user.effective_growth_level() if user is not None else 1,
        'has_password': bool(getattr(user, 'has_password', False)),
        'verified': bool(getattr(user, 'verified', False)) or email_verified or phone_verified,
        'email_verified': email_verified,
        'phone_verified': phone_verified,
        'dual_verified': email_verified and phone_verified,
        'permanent_vip': bool(getattr(user, 'is_permanent_vip', False)),
        'official': bool(getattr(user, 'is_official', False)),
        'chat_enabled': bool(getattr(space, 'chat_enabled', True)),
        'square_enabled': bool(
            getattr(space, 'group_square_enabled', True)
            and getattr(space, 'verification_tier', 'phone') != 'email'
        ),
        'square_explore_enabled': bool(getattr(space, 'square_explore_enabled', True)),
        'space_phone_verified': bool(getattr(space, 'admin_phone_verified_at', None)),
        'space_identity_verified': bool(getattr(space, 'identity_verified_at', False)),
        'unverified_group_policy': int(getattr(space, 'unverified_group_policy', 2)),
        'qr_invite': False,
    }
    values.update(overrides or {})
    return values


@dataclass
class CapabilityDecision:
    key: str
    allowed: bool
    context: dict
    limits: dict = field(default_factory=dict)
    failed: list = field(default_factory=list)
    denied_by: list = field(default_factory=list)

    def payload(self, include_context=False):
        value = {
            'key': self.key,
            'allowed': self.allowed,
            'limits': self.limits,
            'failed': self.failed,
            'denied_by': self.denied_by,
        }
        if include_context:
            value['context'] = self.context
        return value


def _merge_limits(current, incoming):
    result = dict(current)
    for key, value in (incoming or {}).items():
        result[key] = min(result[key], value) if key in result else value
    return result


def evaluate_capability(
        key, user=None, space=None, context=None, platform_overrides=None, space_overrides=None,
        platform_policies=None, space_policies=None):
    definition = get_capability(key)
    if definition is None:
        raise AccessPolicyErrors.CAPABILITY_NOT_FOUND
    space = space or getattr(user, 'space', None)
    values = subject_context(user=user, space=space, overrides=context)
    chain = ancestors(key)
    keys = [item.key for item in chain]
    platform_by_key = platform_policies
    space_by_key = space_policies
    cache_owner = user or space
    if platform_by_key is None and cache_owner is not None:
        platform_by_key = getattr(cache_owner, '_platform_capability_policy_cache', None)
    if platform_by_key is None:
        platform_by_key = {item.capability_key: item for item in PlatformCapabilityPolicy.objects.all()}
        if cache_owner is not None:
            cache_owner._platform_capability_policy_cache = platform_by_key
    if space_by_key is None and cache_owner is not None:
        space_by_key = getattr(cache_owner, '_space_capability_policy_cache', None)
    if space_by_key is None:
        space_by_key = {}
        if space is not None:
            space_by_key = {item.capability_key: item for item in SpaceCapabilityPolicy.objects.filter(space=space)}
        if cache_owner is not None:
            cache_owner._space_capability_policy_cache = space_by_key
    platform_by_key = dict(platform_by_key)
    space_by_key = dict(space_by_key)
    if platform_overrides:
        platform_by_key.update(platform_overrides)
    if space_overrides:
        space_by_key.update(space_overrides)

    failed = []
    denied_by = []
    limits = {}
    for item in chain:
        checks = [('default', item.requirement or {})]
        platform_policy = platform_by_key.get(item.key)
        space_policy = space_by_key.get(item.key)
        if platform_policy is not None:
            checks.append(('platform', platform_policy.requirement or {}))
            limits = _merge_limits(limits, platform_policy.limits)
            if platform_policy.denial and evaluate_expression(platform_policy.denial, values):
                denied_by.append({'key': item.key, 'source': 'platform'})
        if space_policy is not None:
            checks.append(('space', space_policy.requirement or {}))
            limits = _merge_limits(limits, space_policy.limits)
            if space_policy.denial and evaluate_expression(space_policy.denial, values):
                denied_by.append({'key': item.key, 'source': 'space'})
        for source, expression in checks:
            if expression and not evaluate_expression(expression, values):
                failed.append({'key': item.key, 'source': source, 'expression': expression})
    return CapabilityDecision(
        key=key,
        allowed=not failed and not denied_by,
        context=values,
        limits=limits,
        failed=failed,
        denied_by=denied_by,
    )


def require_capability(key, user=None, space=None, context=None):
    decision = evaluate_capability(key, user=user, space=space, context=context)
    if not decision.allowed:
        raise AccessPolicyErrors.CAPABILITY_DENIED(capability=key, decision=decision.payload())
    return decision
