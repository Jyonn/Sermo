import json

from django.db import transaction
from django.views import View
from smartdjango import OK

from AccessPolicy.catalog import CAPABILITIES, catalog_payload, get_capability
from AccessPolicy.engine import evaluate_capability, validate_expression, validate_limits
from AccessPolicy.models import CapabilityPolicyAudit, PlatformCapabilityPolicy, SpaceCapabilityPolicy
from AccessPolicy.validators import AccessPolicyErrors
from PlatformAdmin.models import PlatformAuditLog
from Space.models import Space
from utils import auth


def _body(request):
    try:
        return json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        raise AccessPolicyErrors.POLICY_INVALID


def _policy_payload(policy):
    if policy is None:
        return None
    return {
        'requirement': policy.requirement,
        'denial': policy.denial,
        'limits': policy.limits,
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
    return {
        'requirement': validate_expression(data.get('requirement') or {}),
        'denial': validate_expression(data.get('denial') or {}),
        'limits': validate_limits(data.get('limits') or {}),
    }


def _model_snapshot(policy):
    return _policy_payload(policy) or {}


def _simulation_space_context(space, data, scope):
    if scope == 'space':
        tier = getattr(space, 'verification_tier', 'email')
    else:
        tier = data.get('space_verification', 'email')
        if tier not in {'email', 'phone', 'identity'}:
            raise AccessPolicyErrors.POLICY_INVALID
    return tier, {
        'space_phone_verified': tier in {'phone', 'identity'},
        'space_identity_verified': tier == 'identity',
    }


def _simulate(space, data, scope):
    capability_key = str(data.get('capability_key') or '')
    if get_capability(capability_key) is None:
        raise AccessPolicyErrors.CAPABILITY_NOT_FOUND
    draft = _normalized_policy(data.get('policy') or {})
    holder = type('DraftPolicy', (), draft)()
    overrides = {capability_key: holder}
    platform_policies = {item.capability_key: item for item in PlatformCapabilityPolicy.objects.all()}
    space_policies = {
        item.capability_key: item for item in SpaceCapabilityPolicy.objects.filter(space=space)
    } if space is not None else {}
    space_verification, space_context = _simulation_space_context(space, data, scope)
    rows = []
    verification_states = (
        ('none', False, False), ('email', True, False),
        ('phone', False, True), ('dual', True, True),
    )
    for growth_level in range(1, 19):
        for verification, email_verified, phone_verified in verification_states:
            for permanent_vip in (False, True):
                context = {
                    'growth_level': growth_level,
                    'has_password': growth_level > 3,
                    'verified': email_verified or phone_verified,
                    'email_verified': email_verified,
                    'phone_verified': phone_verified,
                    'dual_verified': email_verified and phone_verified,
                    'permanent_vip': permanent_vip,
                    'official': False,
                    **space_context,
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
                rows.append({
                    'growth_level': growth_level,
                    'verification': verification,
                    'permanent_vip': permanent_vip,
                    'allowed': decision.allowed,
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
        return {'catalog': _catalog_with_policies(), 'fields': sorted(__import__('AccessPolicy.engine', fromlist=['ALLOWED_FIELDS']).ALLOWED_FIELDS)}


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
        return {'catalog': _catalog_with_policies(request.space)}


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
