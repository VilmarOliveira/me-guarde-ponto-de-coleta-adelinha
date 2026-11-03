import base64
import io
import re
import sqlite3

import pytest
from PIL import Image
from app import create_app,IMAGE_UPLOAD_MAX_BYTES
from test_blog_icons import site,publish,article_body
from test_customization import csrf


def photo(fmt='PNG',size=(1200,675),color='orange'):
    output=io.BytesIO();Image.new('RGB',size,color).save(output,fmt);return output.getvalue()


def record(site):
    with sqlite3.connect(site[3]) as db:
        db.row_factory=sqlite3.Row
        return dict(db.execute("SELECT * FROM blog_posts WHERE slug='texto-com-icones'").fetchone())


def preview(site,fields):return site[1].post('/painel/blog/previa',data={**fields,'csrf_token':csrf(site[1])})


@pytest.mark.parametrize('fmt,name,mime',[('PNG','capa.png','image/png'),('JPEG','capa.jpg','image/jpeg'),('WEBP','capa.webp','image/webp')])
def test_optional_cover_for_icons_is_above_title_in_article_and_index(site,fmt,name,mime):
    data=photo(fmt);fields=publish(site,cover_image=(io.BytesIO(data),name),cover_alt='Caixa laranja com encomendas')
    row=record(site);assert bytes(row['cover_image'])==data
    page=site[2].get('/blog/'+fields['slug']).text
    assert page.index('class="article-cover"')<page.index('<h1>')
    assert 'alt="Caixa laranja com encomendas"' in page
    assert f'/blog/{fields["slug"]}/capa?v=' in site[2].get('/blog').text
    image=site[2].get('/blog/'+fields['slug']+'/capa');assert image.data==data and image.mimetype==mime
    assert site[2].get('/blog/'+fields['slug']+'/capa',headers={'If-None-Match':image.headers['ETag']}).status_code==304
    editor=site[1].get('/painel/blog?edit='+str(row['id'])).text
    assert 'Imagem de capa — opcional' in editor and '1200 × 675 px' in editor and '16:9' in editor
    assert '900 KB por imagem' in editor and 'PNG, JPG e WebP' in editor and 'Prefira WebP' in editor
    assert 'data:image/' in editor and 'Descrição da imagem para acessibilidade' in editor
    fields.pop('cover_image');response=preview(site,fields)
    assert response.status_code==200 and 'class="article-cover"' in response.json['html']
    payload=re.search(r'src="data:[^;]+;base64,([^"]+)"',response.json['html']).group(1)
    assert base64.b64decode(payload)==data and article_body(response.json['html'])==article_body(page)


def test_no_cover_has_no_image_or_placeholder_and_existing_cover_reused(site):
    fields=publish(site)
    assert 'class="article-cover"' not in site[2].get('/blog/'+fields['slug']).text
    assert 'article-cover' not in preview(site,fields).json['html']
    before=record(site)
    legacy=photo('JPEG');publish(site,layout='legacy',cover_image=(io.BytesIO(legacy),'old.jpg'),cover_alt='Descrição antiga')
    row=record(site)
    publish(site,post_id=str(row['id']),layout='icons',cover_alt='Descrição antiga')
    assert record(site)['cover_image']==legacy
    assert 'alt="Descrição antiga"' in site[2].get('/blog/'+fields['slug']).text
    assert record(site)['id']==before['id'] and record(site)['slug']==before['slug']


def test_preview_upload_remove_and_save_share_the_same_cover_rules(site):
    original=photo();fields=publish(site,cover_image=(io.BytesIO(original),'old.png'),cover_alt='Imagem atual')
    fields.pop('cover_image');old=record(site)
    staged=preview(site,{**fields,'post_id':str(old['id']),'remove_cover':'yes'})
    assert 'class="article-cover"' not in staged.json['html'] and record(site)==old
    new=photo('WEBP',(600,900),'teal')
    staged=preview(site,{**fields,'cover_image':(io.BytesIO(new),'new.webp'),'cover_alt':'Imagem vertical completa'})
    assert base64.b64decode(re.search(r'base64,([^"]+)',staged.json['html']).group(1))==new
    assert record(site)==old
    publish(site,post_id=str(old['id']),cover_image=(io.BytesIO(new),'new.webp'),cover_alt='Imagem vertical completa')
    assert record(site)['cover_image']==new and record(site)['body']==old['body']
    page=site[2].get('/blog/'+fields['slug']).text
    assert 'Imagem vertical completa' in page and 'class="article-cover"' in page
    publish(site,post_id=str(old['id']),remove_cover='yes')
    current=record(site)
    assert current['cover_image'] is None and current['cover_alt']==''
    for field in ('body','slug','title','seo_title','seo_description','layout','toc_enabled','published_at'):assert current[field]==old[field]
    assert 'class="article-cover"' not in site[2].get('/blog/'+fields['slug']).text
    assert f'/blog/{fields["slug"]}/capa' not in site[2].get('/blog').text
    assert site[2].get('/blog/'+fields['slug']+'/capa').status_code==404


@pytest.mark.parametrize('bad',[b'<svg onload="alert(1)"></svg>',b'GIF89a',photo()+b'x'*IMAGE_UPLOAD_MAX_BYTES,b'RIFFxxxxWEBPbroken'])
def test_invalid_upload_preserves_every_existing_field_even_with_removal(site,bad):
    fields=publish(site,cover_image=(io.BytesIO(photo()),'old.png'),cover_alt='Capa preservada')
    fields.pop('cover_image');before=record(site)
    for remove in ('','yes'):
        request={**fields,'post_id':str(before['id']),'cover_alt':'Não salvar','title':'Não salvar','remove_cover':remove,'cover_image':(io.BytesIO(bad),'fake.png')}
        response=site[1].post('/painel/blog',data=request)
        assert response.status_code==400 and 'imagem anterior foi preservada' in response.text
        assert 'data:image/png;base64,' in response.text
        assert record(site)==before
        request['cover_image']=(io.BytesIO(bad),'fake.png')
        assert preview(site,request).status_code==400 and record(site)==before


def test_900kb_boundary_replacement_and_persistence(site):
    data=photo();data+=b'\0'*(IMAGE_UPLOAD_MAX_BYTES-len(data))
    fields=publish(site,cover_image=(io.BytesIO(data),'limite.png'),cover_alt='Capa no limite')
    assert len(record(site)['cover_image'])==IMAGE_UPLOAD_MAX_BYTES
    restarted=create_app({'TESTING':True,'SECRET_KEY':'restart','DATABASE':site[3]}).test_client()
    assert restarted.get('/blog/'+fields['slug']+'/capa').data==data
    assert 'Capa no limite' in restarted.get('/blog/'+fields['slug']).text
    # A deliberate conflicting instruction is rejected, not treated as deletion.
    row=record(site);fields.pop('cover_image')
    response=site[1].post('/painel/blog',data={**fields,'remove_cover':'yes','cover_image':(io.BytesIO(photo()),'capa.png')})
    assert response.status_code==400 and 'Escolha substituir ou remover' in response.text and record(site)==row
