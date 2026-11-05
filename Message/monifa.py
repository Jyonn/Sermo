from django.views import View
from smartdjango import analyse
from utils import auth
from Message.params import MessageParams
from Message.providers.monifa import MonifaProvider
import requests
from utils.middleware import APIPacker


class MonifaOtpView(View):
    @auth.require_user
    @analyse.json(MessageParams.preview_text)
    def post(self,request):
        try:
            result=MonifaProvider.read(request.json.preview_text)
            response=APIPacker.pack(result)
        except (ValueError, requests.RequestException):
            response=APIPacker.pack({'status':'error'})
        response['Cache-Control']='no-store, private'
        response['Referrer-Policy']='no-referrer'
        return response
