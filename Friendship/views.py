from django.views import View
from smartdjango import analyse, OK

from Friendship.models import Friendship, FriendshipStatusChoice
from Friendship.params import FriendshipParams
from Friendship.validators import FriendshipErrors
from utils import auth
from utils.auth import Request


class FriendshipListView(View):
    @auth.require_user
    def get(self, request: Request):
        rows = Friendship.friend_relations_of(request.user)
        result = []
        for friend, relation in rows:
            payload = friend.json_friend()
            payload['responded_at'] = relation.responded_at.timestamp() if relation.responded_at else None
            result.append(payload)
        return result


class FriendshipStatusView(View):
    @auth.require_user
    @analyse.query(FriendshipParams.user_id)
    def get(self, request: Request):
        item = Friendship.between(request.user, request.query.target_user)
        if item is None or item.status != FriendshipStatusChoice.ACCEPTED:
            return dict(is_friend=False)
        return dict(
            is_friend=True,
            friendship=item.json(),
        )


class FriendshipRequestView(View):
    @auth.require_user
    @analyse.json(FriendshipParams.to_user_id)
    def post(self, request: Request):
        item = Friendship.create(
            from_user=request.user,
            to_user=request.json.to_user,
        )
        return item.json()

    @auth.require_user
    def get(self, request: Request):
        incoming = Friendship.pending_incoming(request.user)
        outgoing = Friendship.pending_outgoing(request.user)
        return dict(
            incoming=[item.json() for item in incoming],
            outgoing=[item.json() for item in outgoing],
        )


class FriendshipRequestRespondView(View):
    @auth.require_user
    @analyse.query(FriendshipParams.user_id)
    @analyse.json(FriendshipParams.accept)
    def post(self, request: Request):
        item = Friendship.between(request.user, request.query.target_user)
        if item is None:
            raise FriendshipErrors.NOT_FRIENDS
        if request.json.accept:
            item.accept(request.user)
        else:
            item.reject(request.user)
        return item.json()


class FriendshipRemoveView(View):
    @auth.require_user
    @analyse.query(FriendshipParams.user_id)
    def delete(self, request: Request):
        item = Friendship.between(request.user, request.query.target_user)
        if item is None:
            raise FriendshipErrors.NOT_FRIENDS
        item.remove(request.user)
        return OK


class FriendshipInviteTokenView(View):
    @auth.require_user
    def post(self, request: Request):
        return Friendship.issue_invite_token(request.user)


class FriendshipInviteRedeemView(View):
    @auth.require_user
    @analyse.json(FriendshipParams.token)
    def post(self, request: Request):
        item = Friendship.redeem_invite_token(
            token=request.json.token,
            requester=request.user,
        )
        return item.json()
