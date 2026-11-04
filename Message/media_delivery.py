from django.http import HttpResponse, HttpResponseRedirect


def media_delivery_response(asset, download=False):
    if download and asset.kind == asset.KIND_VIDEO and asset.transcode_status != asset.TRANSCODE_READY:
        response = HttpResponse(status=409)
        response['Retry-After'] = '10'
    else:
        response = HttpResponseRedirect(asset.delivery_uri())
    response['Cache-Control'] = 'no-store'
    return response
