from django.views import View
from smartdjango import analyse, OK

from Friendship.models import Friendship, FriendshipStatusChoice
from Friendship.params import FriendshipParams
from Friendship.validators import FriendshipErrors
from User.models import User
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
    @analyse.json(FriendshipParams.to_user_id, FriendshipParams.source)
    def post(self, request: Request):
        request.user.require_capability('contacts.friend_request')
        item = Friendship.create(
            from_user=request.user,
            to_user=request.json.to_user,
            source=request.json.source,
        )
        return item.json()

    @auth.require_user
    def get(self, request: Request):
        incoming = Friendship.request_history_incoming(request.user)
        outgoing = Friendship.request_history_outgoing(request.user)
        return dict(
            incoming=[item.json() for item in incoming],
            outgoing=[item.json() for item in outgoing],
        )


class FriendshipExactSearchView(View):
    @auth.require_user
    @analyse.query(FriendshipParams.exact_name)
    def get(self, request: Request):
        request.user.require_capability('contacts.search')
        if not request.user.verified:
            raise FriendshipErrors.REQUEST_FORBIDDEN
        normalized_name = User.normalizers.lower_name(request.query.name)
        target = User.objects.filter(
            space=request.user.space,
            is_deleted=False,
            lower_name=normalized_name,
        ).exclude(id=request.user.id).first()
        if target is None:
            return dict(user=None, relationship='none')
        relation = Friendship.between(request.user, target)
        relationship = 'none'
        if relation is not None:
            relationship = {
                FriendshipStatusChoice.PENDING: 'pending',
                FriendshipStatusChoice.ACCEPTED: 'friend',
            }.get(relation.status, 'none')
        return dict(user=target.json_friend(), relationship=relationship)


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
    @analyse.json(FriendshipParams.permanent)
    def post(self, request: Request):
        request.user.require_capability('contacts.qr')
        return Friendship.issue_invite_token(request.user, permanent=bool(request.json.permanent))


class FriendshipInvitePreviewView(View):
    @analyse.query(FriendshipParams.token)
    def get(self, request: Request):
        payload = Friendship.preview_invite_token(request.query.token)
        return dict(
            inviter=payload['inviter'].tiny_json(),
            space=payload['space'].json(),
            expire=payload['expire'],
            permanent=payload['permanent'],
        )


class FriendshipInviteRedeemView(View):
    @auth.require_user
    @analyse.json(FriendshipParams.token)
    def post(self, request: Request):
        request.user.require_capability('contacts.friend_request')
        item = Friendship.redeem_invite_token(
            token=request.json.token,
            requester=request.user,
        )
        return item.json()
