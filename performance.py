"""Public asset delivery only; never cache or compress customer HTML/API data."""
import gzip
from functools import lru_cache
from flask import request

PUBLIC_ENDPOINTS = {'static', 'brand_stylesheet'}
TEXT_TYPES = {'text/css', 'text/javascript', 'application/javascript', 'image/svg+xml'}

@lru_cache(maxsize=32)
def _gzip(data):
    return gzip.compress(data, compresslevel=5, mtime=0)

def install(app):
    @app.after_request
    def public_assets(response):
        if request.endpoint not in PUBLIC_ENDPOINTS or request.method not in {'GET', 'HEAD'}:
            return response
        if response.status_code not in {200, 304} or response.headers.get('Set-Cookie'):
            return response
        response.headers['Cache-Control'] = 'public, max-age=3600'
        if response.mimetype not in TEXT_TYPES:
            return response
        response.vary.add('Accept-Encoding')
        if (request.method != 'GET' or response.status_code != 200 or
                request.range or response.headers.get('Content-Encoding') or
                request.accept_encodings['gzip'] <= 0 or
                not response.content_length or not 1024 <= response.content_length <= 524288):
            return response
        response.direct_passthrough = False
        data = response.get_data()
        compressed = _gzip(data)
        if len(compressed) < len(data):
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            etag, _ = response.get_etag()
            if etag:
                response.set_etag(etag, weak=True)
        return response
