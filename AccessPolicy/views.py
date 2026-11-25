import json

from django.db import transaction
from django.views import View
from smartdjango import OK

from AccessPolicy.catalog import CAPABILITIES, catalog_payload, get_capability
from AccessPolicy.engine import ALLOWED_FIELDS, evaluate_capability, subject_context, validate_expression, validate_limits
from AccessPolicy.models import CapabilityPolicyAudit, PlatformCapabilityPolicy, SpaceCapabilityPolicy
from AccessPolicy.validators import AccessPolicyErrors
from PlatformAdmin.models import PlatformAuditLog
from Space.models import Space
from utils import auth


def _body(request):
    try:
        data = json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AccessPolicyErrors.POLICY_INVALID
    if not isinstance(data, dict):
        raise AccessPolicyErrors.POLICY_INVALID
    return data


def _policy_payload(policy):
    if policy is None:
        return None
    return {
        'requirement': policy.requirement,
        'denial': policy.denial,
        'limits': policy.limits,
        'enabled': policy.enabled,
        'version': policy.version,
        'updated_at': policy.updated_at.timestamp(),
    }


def _catalog_with_policies(space=None):
    platform = {item.capability_key: item for item in PlatformCapabilityPolicy.objects.all()}
    space_policies = {
        item.capability_key: item
        for item in SpaceCapabilityPolicy.objects.filter(space=space)
    } if space is not None else {}

    def decorate(nodes):
        result = []
        for node in nodes:
            item = dict(node)
            item['platform_policy'] = _policy_payload(platform.get(item['key']))
            item['space_policy'] = _policy_payload(space_policies.get(item['key']))
            item['children'] = decorate(item['children'])
            result.append(item)
        return result

    return decorate(catalog_payload())


def _normalized_policy(data):
    if not isinstance(data, dict):
        raise AccessPolicyErrors.POLICY_INVALID
    result = {
        'requirement': validate_expression(data.get('requirement') or {}),
        'denial': validate_expression(data.get('denial') or {}),
        'limits': validate_limits(data.get('limits') or {}),
    }
    if 'enabled' in data:
        if type(data['enabled']) is not bool:
            raise AccessPolicyErrors.POLICY_INVALID
        result['enabled'] = data['enabled']
    return result


def _model_snapshot(policy):
    return _policy_payload(policy) or {}


def _simulation_space_context(space, data, scope):
    context = subject_context(space=space)
    if scope == 'space':
        tier = space.verification_tier
    else:
        tier = data.get('space_verification', 'email')
        if tier not in {'email', 'phone', 'identity'}:
            raise AccessPolicyErrors.POLICY_INVALID
        for key in ('chat_enabled', 'square_enabled', 'square_free_post_enabled', 'square_explore_enabled'):
            value = data.get(key, True)
            if type(value) is not bool:
                raise AccessPolicyErrors.POLICY_INVALID
            context[key] = value
        context['square_enabled'] = context['square_enabled'] and tier != 'email'
        mode = data.get('unverified_group_policy', 2)
        if type(mode) is not int or mode not in (0, 1, 2):
            raise AccessPolicyErrors.POLICY_INVALID
        context['unverified_group_policy'] = mode
    return tier, {**context,
        'space_phone_verified': tier in {'phone', 'identity'},
        'space_identity_verified': tier == 'identity',
    }


def _module_failures(key, context):
    """These are the feature checks made by the normal chat and Square entry points."""
    fields = []
    if key == 'chat' or key.startswith('chat.'):
        fields.append('chat_enabled')
    if key == 'square' or key.startswith('square.'):
        fields.append('square_enabled')
    if key == 'square.explore':
        fields.append('square_explore_enabled')
    if key == 'square.statement.publish' or key.startswith('square.statement.publish.'):
        fields.append('square_free_post_enabled')
    return [
        {'key': key, 'source': 'module', 'reason': field}
        for field in fields if not context[field]
    ]


def _simulate(space, data, scope):
    capability_key = str(data.get('capability_key') or '')
    if get_capability(capability_key) is None:
        raise AccessPolicyErrors.CAPABILITY_NOT_FOUND
    draft = _normalized_policy(data.get('policy') or {})
    platform_policies = {item.capability_key: item for item in PlatformCapabilityPolicy.objects.all()}
    space_policies = {
        item.capability_key: item for item in SpaceCapabilityPolicy.objects.filter(space=space)
    } if space is not None else {}
    saved = (platform_policies if scope == 'platform' else space_policies).get(capability_key)
    draft.setdefault('enabled', saved.enabled if saved is not None else True)
    holder = type('DraftPolicy', (), draft)()
    overrides = {capability_key: holder}
    space_verification, space_context = _simulation_space_context(space, data, scope)
    subject = {}
    for key in ('official', 'has_password', 'qr_invite', 'wechat_miniprogram'):
        if key in data:
            if type(data[key]) is not bool:
                raise AccessPolicyErrors.POLICY_INVALID
            subject[key] = data[key]
    flash_forward = data.get('flash_forward', False)
    if type(flash_forward) is not bool:
        raise AccessPolicyErrors.POLICY_INVALID
    if scope == 'space' and 'unverified_group_policy' in data:
        mode = data['unverified_group_policy']
        if type(mode) is not int or mode not in (0, 1, 2):
            raise AccessPolicyErrors.POLICY_INVALID
        space_context['unverified_group_policy'] = mode
    from Square.models import effective_frequency_limits
    publishing = capability_key == 'square.statement.publish' or capability_key.startswith('square.statement.publish.')
    rows = []
    verification_states = (
        ('none', False, False), ('email', True, False),
        ('phone', False, True), ('dual', True, True),
    )
    for growth_level in range(1, 19):
        for verification, email_verified, phone_verified in verification_states:
            for permanent_vip in (False, True):
                context = {
                    **space_context,
                    'growth_level': growth_level,
                    'has_password': False,
                    'verified': email_verified or phone_verified,
                    'email_verified': email_verified,
                    'phone_verified': phone_verified,
                    'dual_verified': email_verified and phone_verified,
                    'permanent_vip': permanent_vip,
                    'official': False,
                    **subject,
                }
                decision = evaluate_capability(
                    capability_key,
                    space=space,
                    context=context,
                    platform_overrides=overrides if scope == 'platform' else None,
                    space_overrides=overrides if scope == 'space' else None,
                    platform_policies=platform_policies,
                    space_policies=space_policies,
                )
                module_failures = _module_failures(capability_key, context)
                if capability_key == 'chat.message.flash_image' and not flash_forward:
                    image = evaluate_capability('chat.message.send.image', space=space, context=context,
                                                platform_policies=platform_policies, space_policies=space_policies)
                    decision.failed += [item for item in image.failed if item not in decision.failed]
                    decision.denied_by += [item for item in image.denied_by if item not in decision.denied_by]
                effective_limits = {}
                if publishing:
                    # Media-specific policies govern access. The posting quota is shared.
                    publish_decision = decision if capability_key == 'square.statement.publish' else evaluate_capability(
                        'square.statement.publish', space=space, context=context,
                        platform_policies=platform_policies, space_policies=space_policies,
                    )
                    daily, weekly = effective_frequency_limits(growth_level, permanent_vip, publish_decision.limits)
                    effective_limits = {'daily': daily, 'weekly': weekly}
                rows.append({
                    'growth_level': growth_level,
                    'verification': verification,
                    'permanent_vip': permanent_vip,
                    'allowed': not (decision.failed or decision.denied_by or module_failures),
                    'failed': decision.failed,
                    'denied_by': module_failures + decision.denied_by,
                    'limits': effective_limits,
                })
    return {
        'capability_key': capability_key,
        'space_verification': space_verification,
        'rows': rows,
    }


class UserCapabilityView(View):
    @auth.require_user
    def get(self, request):
        requested = [value.strip() for value in request.GET.get('keys', '').split(',') if value.strip()]
        keys = requested or list(CAPABILITIES)
        decisions = {}
        for key in keys:
            if key in CAPABILITIES:
                decisions[key] = evaluate_capability(key, user=request.user).payload()
        return {'version': 1, 'capabilities': decisions}


class PlatformPolicyListView(View):
    @auth.require_platform_admin
    def get(self, request):
        return {'catalog': _catalog_with_policies(), 'fields': sorted(ALLOWED_FIELDS)}


class PlatformPolicyDetailView(View):
    @auth.require_platform_admin
    @transaction.atomic
    def post(self, request, capability_key):
        if get_capability(capability_key) is None:
            raise AccessPolicyErrors.CAPABILITY_NOT_FOUND
        values = _normalized_policy(_body(request))
        policy = PlatformCapabilityPolicy.objects.select_for_update().filter(capability_key=capability_key).first()
        previous = _model_snapshot(policy)
        if policy is None:
            policy = PlatformCapabilityPolicy(capability_key=capability_key, updated_by=request.platform_admin_email)
        else:
            policy.version += 1
        for key, value in values.items():
            setattr(policy, key, value)
        policy.updated_by = request.platform_admin_email
        policy.save()
        CapabilityPolicyAudit.objects.create(
            scope=CapabilityPolicyAudit.SCOPE_PLATFORM,
            capability_key=capability_key,
            actor=request.platform_admin_email,
            previous=previous,
            current=_model_snapshot(policy),
        )
        PlatformAuditLog.objects.create(
            action='permission.platform_updated', target_type='capability',
            summary=f'更新平台能力 {capability_key}', metadata={'capability_key': capability_key, 'version': policy.version},
        )
        return _policy_payload(policy)

    @auth.require_platform_admin
    @transaction.atomic
    def delete(self, request, capability_key):
        policy = PlatformCapabilityPolicy.objects.select_for_update().filter(capability_key=capability_key).first()
        if policy is not None:
            previous = _model_snapshot(policy)
            policy.delete()
            CapabilityPolicyAudit.objects.create(
                scope=CapabilityPolicyAudit.SCOPE_PLATFORM, capability_key=capability_key,
                actor=request.platform_admin_email, previous=previous, current={},
            )
        return OK


class PlatformPolicySimulateView(View):
    @auth.require_platform_admin
    def post(self, request):
        return _simulate(None, _body(request), 'platform')


class SpacePolicyListView(View):
    @auth.require_space
    def get(self, request):
        context = subject_context(space=request.space)
        return {'catalog': _catalog_with_policies(request.space), 'fields': sorted(ALLOWED_FIELDS),
                'space_context': {key: context[key] for key in ('chat_enabled', 'square_enabled',
                    'square_free_post_enabled', 'square_explore_enabled', 'unverified_group_policy')}}


class SpaceGroupParticipationView(View):
    @auth.require_space
    @transaction.atomic
    def post(self, request):
        mode = _body(request).get('unverified_group_policy')
        if type(mode) is not int or mode not in (0, 1, 2):
            raise AccessPolicyErrors.POLICY_INVALID
        space = Space.objects.select_for_update().get(pk=request.space.pk)
        previous = space.unverified_group_policy
        if mode != previous:
            space.unverified_group_policy = mode
            space.save(update_fields=['unverified_group_policy'])
            CapabilityPolicyAudit.objects.create(
                scope=CapabilityPolicyAudit.SCOPE_SPACE, space=space, capability_key='chat.group',
                actor=request.space.email, previous={'unverified_group_policy': previous},
                current={'unverified_group_policy': mode},
            )
        return {'unverified_group_policy': mode}


class SpacePolicyDetailView(View):
    @auth.require_space
    @transaction.atomic
    def post(self, request, capability_key):
        definition = get_capability(capability_key)
        if definition is None:
            raise AccessPolicyErrors.CAPABILITY_NOT_FOUND
        if not definition.space_configurable:
            raise AccessPolicyErrors.SPACE_POLICY_FORBIDDEN
        values = _normalized_policy(_body(request))
        policy = SpaceCapabilityPolicy.objects.select_for_update().filter(space=request.space, capability_key=capability_key).first()
        previous = _model_snapshot(policy)
        if policy is None:
            policy = SpaceCapabilityPolicy(space=request.space, capability_key=capability_key)
        else:
            policy.version += 1
        for key, value in values.items():
            setattr(policy, key, value)
        policy.save()
        CapabilityPolicyAudit.objects.create(
            scope=CapabilityPolicyAudit.SCOPE_SPACE, space=request.space,
            capability_key=capability_key, actor=request.space.email,
            previous=previous, current=_model_snapshot(policy),
        )
        return _policy_payload(policy)

    @auth.require_space
    @transaction.atomic
    def delete(self, request, capability_key):
        policy = SpaceCapabilityPolicy.objects.select_for_update().filter(space=request.space, capability_key=capability_key).first()
        if policy is not None:
            previous = _model_snapshot(policy)
            policy.delete()
            CapabilityPolicyAudit.objects.create(
                scope=CapabilityPolicyAudit.SCOPE_SPACE, space=request.space,
                capability_key=capability_key, actor=request.space.email,
                previous=previous, current={},
            )
        return OK


class SpacePolicySimulateView(View):
    @auth.require_space
    def post(self, request):
        return _simulate(request.space, _body(request), 'space')
