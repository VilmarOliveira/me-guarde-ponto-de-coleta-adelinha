"""Isolated visual fixture; temporary DB and no real email or Google calls."""
import io
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from PIL import Image
from werkzeug.security import generate_password_hash
import app as application
from app import create_app
from test_customization import csrf,legacy_login
TEMP=tempfile.TemporaryDirectory(prefix='adelinha-blog-')
application.send_code=lambda *args:(False,'disabled in fixture')
app=create_app({'TESTING':True,'SECRET_KEY':'blog-visual-test','DATABASE':str(Path(TEMP.name)/'blog.db'),'STAFF_USER':'blog-admin','STAFF_PASSWORD_HASH':generate_password_hash('blog-fixture-password')})
for fmt in ('PNG','WEBP'):
    image=Image.new('RGBA',(128,128),(0,156,160,255));image.save('/tmp/blog-icon-fixture.'+fmt.lower(),fmt)
admin=app.test_client();legacy_login(admin)
admin.post('/painel/blog',data={'title':'Artigo legado com capa','slug':'legado-preservado','summary':'A capa e o texto antigos são preservados.','body':'## Texto anterior\n\nArtigo antigo com capa e informações completas.','action':'publish','cover_image':(io.BytesIO(Path('/tmp/blog-icon-fixture.png').read_bytes()),'capa.png'),'csrf_token':csrf(admin)})
if __name__=='__main__':app.run(host='127.0.0.1',port=5005)
