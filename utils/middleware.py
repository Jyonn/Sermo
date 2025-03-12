from django.http import HttpResponse

from utils import api_packer
from utils.error import Error


class APIMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, r, *args, **kwargs):
        resp = self.get_response(r, *args, **kwargs)
        if isinstance(resp, HttpResponse):
            return resp

        return api_packer.pack(resp)

    @staticmethod
    def process_exception(_, error):
        if isinstance(error, Error):
            return api_packer.pack(error)
