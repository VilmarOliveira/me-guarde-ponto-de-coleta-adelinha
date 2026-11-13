import gzip
from flask import Flask, Response
from performance import install


def asset_app(tmp_path):
    (tmp_path/'sample.css').write_text('body{color:teal;}\n'*300)
    app=Flask(__name__,static_folder=str(tmp_path),static_url_path='/static')
    app.secret_key='fixture'
    install(app)
    @app.get('/private')
    def private():
        return Response('private '*1000,headers={'Cache-Control':'private, no-store'})
    return app


def test_asset_compression_and_cache(tmp_path):
    app=asset_app(tmp_path); client=app.test_client()
    plain=client.get('/static/sample.css')
    packed=client.get('/static/sample.css',headers={'Accept-Encoding':'gzip'})
    assert gzip.decompress(packed.data)==plain.data
    assert len(packed.data)<len(plain.data)//2
    assert 'Accept-Encoding' in packed.vary
    assert packed.headers['Cache-Control']=='public, max-age=3600'
    assert 'Content-Encoding' not in client.get('/static/sample.css',headers={'Accept-Encoding':'gzip;q=0'}).headers
    assert 'Content-Encoding' not in client.get('/static/sample.css',headers={'Accept-Encoding':'gzip','Range':'bytes=0-99'}).headers
    assert client.get('/static/sample.css',headers={'If-None-Match':plain.headers['ETag']}).status_code==304


def test_private_responses_are_never_compressed_or_shared(tmp_path):
    app=asset_app(tmp_path); response=app.test_client().get('/private',headers={'Accept-Encoding':'gzip'})
    assert response.headers['Cache-Control']=='private, no-store'
    assert 'Content-Encoding' not in response.headers


def test_real_app_public_assets_do_not_create_session(tmp_path):
    from app import create_app
    app=create_app({'TESTING':True,'DATABASE':str(tmp_path/'asset.db'),'SECRET_KEY':'fixture'})
    client=app.test_client()
    response=client.get('/static/interface.css',headers={'Accept-Encoding':'gzip'})
    assert response.status_code==200
    assert response.headers['Content-Encoding']=='gzip'
    assert 'Set-Cookie' not in response.headers
    assert 'Cookie' not in response.vary
    with client.session_transaction() as session:
        session['staff']='fixture'
    response=client.get('/static/interface.js',headers={'Accept-Encoding':'gzip'})
    assert response.headers['Cache-Control']=='public, max-age=3600'
    assert 'Cookie' not in response.vary
    private=client.get('/painel')
    assert 'no-store' in private.headers['Cache-Control']
