import datetime

from django.views import View
from django.utils import timezone
from notificator import NotificatorAPIError
from smartdjango import analyse

from Space import auth as space_auth
from Space.models import Space
from Space.models import SpaceEmailVerificationCode, SpaceEmailCodePurposeChoice
from Space.params import (
    SpaceParams,
    SpaceEmailVerificationCodeParams,
    SpaceUserListParams,
)
from Space.validators import SpaceErrors
from utils import auth
from utils.auth import Request
from User.models import User
from utils.global_settings import notificator


class SpaceEmailCodeRequestView(View):
    @analyse.json(
        SpaceEmailVerificationCodeParams.slug,
        SpaceEmailVerificationCodeParams.email,
    )
    def post(self, request: Request):
        slug = request.json.slug
        email = request.json.email

        if slug:
            space = Space.get_by_slug(slug)
            if space.email != email.strip().lower():
                raise SpaceErrors.EMAIL_MISMATCH
            purpose = SpaceEmailCodePurposeChoice.LOGIN
        else:
            space = None
            purpose = SpaceEmailCodePurposeChoice.REGISTER

        verify_code = SpaceEmailVerificationCode.issue(
            email=email,
            purpose=purpose,
            space=space,
        )
        title = 'Sermo space verification code'
        body = f'Your verification code is {verify_code.code}. It expires in {SpaceEmailVerificationCode.EXPIRE_SECONDS // 60} minutes.'
        try:
            notificator.mail(
                target=verify_code.email,
                title=title,
                body=body,
                recipient_name='Space Admin',
            )
        except NotificatorAPIError as e:
            raise SpaceErrors.NOTIFICATOR_FAILED(details=e)
        return dict(expires_in=SpaceEmailVerificationCode.EXPIRE_SECONDS)


class SpaceView(View):
    @analyse.json(
        SpaceParams.name,
        SpaceParams.slug,
        SpaceParams.email,
        SpaceEmailVerificationCodeParams.code,
    )
    def post(self, request: Request):
        space = Space.create(
            name=request.json.name,
            slug=request.json.slug,
            email=request.json.email,
            code=request.json.code,
        )
        return dict(
            space=space.json(),
            auth=space_auth.get_login_token(space),
        )


class SpaceLoginView(View):
    @analyse.json(
        SpaceParams.slug,
        SpaceEmailVerificationCodeParams.email,
        SpaceEmailVerificationCodeParams.code,
    )
    def post(self, request: Request):
        space = Space.login_by_email_code(
            slug=request.json.slug,
            email=request.json.email,
            code=request.json.code,
        )
        return dict(
            space=space.json(),
            auth=space_auth.get_login_token(space),
        )


class SpaceJoinView(View):
    @analyse.json(
        SpaceParams.slug,
        SpaceParams.name,
        SpaceParams.password,
    )
    def post(self, request: Request):
        space = Space.get_by_slug(request.json.slug)
        user = User.login(
            space=space,
            name=request.json.name,
            password=request.json.password,
        )
        return dict(
            space=space.json(),
            auth=auth.get_login_token(user),
        )


class SpaceMeView(View):
    @auth.require_user
    def get(self, request: Request):
        return request.user.space.json()


class SpaceUserListView(View):
    force_online = None

    @auth.require_user
    @analyse.query(
        SpaceUserListParams.q,
        SpaceUserListParams.online,
        SpaceUserListParams.limit,
        SpaceUserListParams.offset,
    )
    def get(self, request: Request):
        users = User.objects.filter(space=request.user.space, is_deleted=False)

        if request.query.q:
            users = users.filter(lower_name__contains=request.query.q.lower())

        online = request.query.online
        if self.force_online is not None:
            online = 1 if self.force_online else 0
        if online is not None:
            threshold = timezone.now() - datetime.timedelta(minutes=User.vldt.OFFLINE_MIN_INTERVAL)
            if bool(online):
                users = users.filter(last_heartbeat__gt=threshold)
            else:
                users = users.filter(last_heartbeat__lte=threshold)

        offset = request.query.offset
        limit = request.query.limit
        rows = users.order_by('-last_heartbeat', 'id')[offset:offset + limit]
        return [user.json() for user in rows]
