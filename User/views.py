from django.views import View

from Chat.models import SingleChat
from User import auth
from User.models import HostUser, GuestUser
from User.params import HostUserParams, GuestUserParams
from utils import analyse
from utils.analyse import Request
from utils.error import OK


class HostLoginView(View):
    @analyse.body(HostUserParams.name, HostUserParams.password)
    def post(self, request: Request):
        host = HostUser.login(request.body.name, request.body.password)
        return auth.get_login_token(host)


class GuestLoginView(View):
    @analyse.body(GuestUserParams.name, GuestUserParams.password, GuestUserParams.host_id)
    def post(self, request: Request):
        guest = GuestUser.login(request.body.name, request.body.password, request.body.host)

        SingleChat.get_or_create(guest)
        return auth.get_login_token(guest)


class HeartbeatView(View):
    @auth.require_user
    def get(self, request: Request):
        request.user.heartbeat()
        return OK
