import html
import io
import re
import sqlite3
from pathlib import Path

import pytest
from PIL import Image
from app import create_app
from blog_content import render_article,BUILTINS,ALIASES
from blog_tools import ICON_MAX_BYTES
from test_customization import csrf,legacy_login

@pytest.fixture
def site(tmp_path):
    database=str(tmp_path/'blog.db');app=create_app({'TESTING':True,'SECRET_KEY':'blog-test','DATABASE':database})
    admin=app.test_client();legacy_login(admin)
    return app,admin,app.test_client(),database


def image(fmt='PNG',color=(0,165,165,255)):
    output=io.BytesIO();Image.new('RGBA',(128,128),color).convert('RGB' if fmt=='JPEG' else 'RGBA').save(output,fmt);return output.getvalue()


def publish(site,**fields):
    admin=site[1];data={'title':'Artigo completo com ícones','summary':'Introdução resumida com ação e segurança.','slug':'texto-com-icones','body':'Introdução do artigo.\n\n[icone:localizacao] Use nosso endereço\n\nInforme nome completo + ID.\n\n[icone:identificacao] Identifique seu pacote\n\nNão corte este texto.','layout':'icons','toc_enabled':'yes','seo_title':'SEO preservado','seo_description':'Descrição do artigo','action':'publish',**fields,'csrf_token':csrf(admin)}
    result=admin.post('/painel/blog',data=data)
    assert result.status_code==302,result.text
    return data


def upload(site,identifier='sacola',data=None,filename='icone.png'):
    return site[1].post('/painel/blog/icones',data={'name':'Sacola transparente','identifier':identifier,'image':(io.BytesIO(data if data is not None else image()),filename),'csrf_token':csrf(site[1])},headers={'X-Requested-With':'XMLHttpRequest'})


def article_body(page):return page.partition('<div class="article-body">')[2].partition('</div><p><a')[0]


def test_long_complete_text_icons_lists_links_preview_toc_and_seo(site):
    text='Introdução ágil, café e ação.\n\n'+('Texto extenso com espaços e acentos: coração, endereço e identificação.\n\n'*180)+'[icone:localizacao] Seção repetida\n\n- Nome completo + ID\n- Endereço correto\n\n[icone:sino] Seção repetida\n\nVeja [atendimento](/duvidas) e https://example.com/compra?q=1&x=2\n\n### Horário detalhado\n\n> **Importante**: retire com documento.\n\n1. Primeiro passo\n2. Último passo\n\nFIM DO ARTIGO SEM CORTE.'
    assert len(text)>8000
    data=publish(site,body=text)
    preview=site[1].post('/painel/blog/previa',data=data)
    assert preview.status_code==200 and preview.json['warnings']==[]
    page=site[2].get('/blog/'+data['slug']);assert page.status_code==200
    assert article_body(preview.json['html'])==article_body(page.text)
    assert 'FIM DO ARTIGO SEM CORTE.' in page.text and page.text.count('Texto extenso com espaços')==180
    assert 'href="/duvidas"' in page.text and 'https://example.com/compra?q=1&amp;x=2' in page.text
    assert 'blog-icons/localizacao.svg' in page.text and 'blog-icons/sino.svg' in page.text
    assert 'alt="" aria-hidden="true"' in page.text
    ids=re.findall(r'id="(blog-secao-\d+)"',page.text);assert ids==['blog-secao-1','blog-secao-2','blog-secao-3']
    for anchor in ids:assert f'href="#{anchor}"' in page.text
    assert page.text.index('Texto extenso com espaços')<page.text.index('Índice do artigo')<page.text.index('id="blog-secao-1"')
    assert page.text.count('<h1>')==1 and '<h3 class="blog-heading"' in page.text
    assert '<title>SEO preservado</title>' in page.text and '<meta name="description" content="Descrição do artigo">' in page.text
    assert 'rel="canonical" href="http://localhost/blog/texto-com-icones"' in page.text
    with sqlite3.connect(site[3]) as db:assert db.execute('SELECT body FROM blog_posts WHERE slug=?',(data['slug'],)).fetchone()[0]==html.escape(text,quote=True)


def test_only_line_start_unknown_markers_and_html_are_safe(site):
    text='No parágrafo [icone:sino] continua literal.\n\n [icone:localizacao] Não converter com espaço inicial.\n\n[icone:nao-existe] Título íntegro\n\n<script>alert(1)</script>\n\n[clique](javascript:alert(1))\n\n[icone:seguranca] Segurança <img src=x onerror=alert(2)>\n\nAcentos preservados.'
    data=publish(site,body=text)
    response=site[1].post('/painel/blog/previa',data=data)
    assert any('nao-existe' in message for message in response.json['warnings'])
    page=site[2].get('/blog/'+data['slug']).text
    assert 'No parágrafo [icone:sino] continua literal.' in page and '[icone:localizacao] Não converter' in page
    assert '[icone:nao-existe]' not in page and 'Título íntegro' in page
    assert '<script>alert(1)</script>' not in page and '&lt;script&gt;' in page
    assert 'href="javascript:' not in page and '<img src=x' not in page
    assert 'blog-icons/seguranca.svg' in page
    assert site[1].post('/painel/blog/previa',data={**data,'toc_enabled':''}).json['html'].count('class="blog-toc"')==0


def test_custom_icons_replace_archive_cache_versions_and_restart(site):
    response=upload(site);assert response.status_code==200
    icon=response.json['icon'];old_url=icon['src'];original=image()
    saved=site[2].get(old_url);assert saved.data==original and saved.mimetype=='image/png'
    assert 'immutable' in saved.headers['Cache-Control'] and site[2].get(old_url,headers={'If-None-Match':saved.headers['ETag']}).status_code==304
    data=publish(site,body='[icone:sacola] Sacola\n\nTexto completo.')
    assert old_url in site[2].get('/blog/'+data['slug']).text
    assert 'class="blog-toc"' in site[2].get('/blog/'+data['slug']).text
    newer=upload(site,data=image('WEBP'),filename='nova.webp');assert newer.status_code==200
    new_url=newer.json['icon']['src'];assert new_url!=old_url
    assert old_url not in site[2].get('/blog/'+data['slug']).text and new_url in site[2].get('/blog/'+data['slug']).text
    assert site[2].get(old_url).data==original # earlier cached articles remain valid
    result=site[1].post('/painel/blog/icones/sacola/remover',data={'csrf_token':csrf(site[1])},headers={'X-Requested-With':'XMLHttpRequest'})
    assert result.status_code==200
    assert new_url in site[2].get('/blog/'+data['slug']).text
    editor=site[1].get('/painel/blog').text
    assert 'data-blog-icon="sacola"' not in editor
    assert site[1].post('/painel/blog/previa',data=data).json['warnings']==[]
    restarted=create_app({'TESTING':True,'SECRET_KEY':'restart','DATABASE':site[3]}).test_client()
    assert restarted.get(new_url).data==image('WEBP') and new_url in restarted.get('/blog/'+data['slug']).text


@pytest.mark.parametrize('payload,filename',[(b'<svg onload="alert(1)"></svg>','fake.png'),(image('JPEG'),'fake.png'),(image()+b'x'*(ICON_MAX_BYTES+1),'large.webp'),(b'RIFFxxxxWEBPbroken','fake.webp')])
def test_invalid_icon_preserves_previous_image(site,payload,filename):
    first=upload(site);assert first.status_code==200
    rejected=upload(site,data=payload,filename=filename)
    assert rejected.status_code==400 and 'preservada' in rejected.json['message']
    assert site[2].get(first.json['icon']['src']).data==image()
    with sqlite3.connect(site[3]) as db:assert db.execute('SELECT asset_hash FROM blog_icons WHERE identifier=?',('sacola',)).fetchone()[0] in first.json['icon']['src']


def test_reserved_ids_and_permissions_preserve_blog_editor_acl(site):
    assert upload(site,identifier='localizacao').status_code==400
    assert upload(site,identifier='documento').status_code==400
    assert upload(site,identifier='../unsafe').status_code==400
    assert site[2].get('/painel/blog').status_code==302
    assert site[2].post('/painel/blog/previa',data={'body':'Private','csrf_token':csrf(site[2])}).status_code==302
    with sqlite3.connect(site[3]) as db:db.execute("INSERT INTO staff_users(id,name,email,password_hash,role,can_settings,created_at) VALUES(90,'Editor delegado','editor@example.test','hash','gerente',1,'2026-01-01')")
    editor=site[0].test_client()
    with editor.session_transaction() as session:session.update(staff='editor',staff_user_id=90,staff_session_version=1)
    assert editor.get('/painel/blog').status_code==200
    assert 'data-blog-icon-upload' not in editor.get('/painel/blog').text
    assert editor.post('/painel/blog/previa',data={'body':'Texto seguro','csrf_token':csrf(editor)}).status_code==200
    assert editor.post('/painel/blog/icones',data={'csrf_token':csrf(editor)}).status_code==403
    assert editor.post('/painel/blog/icones/sacola/remover',data={'csrf_token':csrf(editor)}).status_code==403
    assert site[1].post('/painel/blog/icones',data={}).status_code==400 # CSRF still enforced


def test_legacy_articles_images_urls_and_escaped_editor_round_trip_preserved(site):
    cover=image();raw='## Legado\n\nTexto com <script>literal</script> e & sinais.\n\n[icone:sino] Era texto literal.'
    data=publish(site,body=raw,layout='legacy',cover_image=(io.BytesIO(cover),'capa.png'))
    before=site[2].get('/blog/'+data['slug']).text
    assert 'class="article-cover"' in before and '[icone:sino] Era texto literal.' in before
    assert 'blog-heading-icon' not in before
    with sqlite3.connect(site[3]) as db:pid=db.execute('SELECT id FROM blog_posts WHERE slug=?',(data['slug'],)).fetchone()[0]
    editor=site[1].get('/painel/blog?edit='+str(pid)).text
    assert html.escape(raw,quote=True) in editor and '&amp;lt;script' not in editor
    assert 'value="legacy" selected' in editor
    publish(site,body=raw,layout='icons',post_id=str(pid))
    assert 'class="article-cover"' in site[2].get('/blog/'+data['slug']).text
    assert site[2].get('/blog/'+data['slug']+'/capa').data==cover
    assert site[1].post('/painel/blog',data={'title':'Mover','slug':'outro-slug','post_id':str(pid),'body':raw,'layout':'icons','csrf_token':csrf(site[1])}).status_code==400
    assert site[2].get('/blog/'+data['slug']).status_code==200
    assert site[2].get('/blog/outro-slug').status_code==404


def test_svg_library_distinct_and_no_new_editorial_limit():
    assert len(BUILTINS)==12 and len({value[1] for value in BUILTINS.values()})==12
    for name in BUILTINS:assert Path('static/blog-icons',name+'.svg').is_file()
    assert all(value in BUILTINS for value in ALIASES.values())
    rendered=render_article(html.escape('[icone:pacote] Título\n\n'+'á'*50000),{'pacote':{'src':'/safe.svg'}})
    assert 'á'*50000 in rendered['sections'][0]['html']
    malformed=render_article(html.escape('[icone:sino] URL\n\nhttps://[invalid'),{})
    assert 'https://[invalid' in malformed['sections'][0]['html']


def test_upgrade_never_converts_existing_articles_or_removes_covers(site):
    cover=image();data=publish(site,layout='legacy',body='Texto anterior\n\n[icone:sino] Literal antes da atualização.',cover_image=(io.BytesIO(cover),'capa.png'))
    with sqlite3.connect(site[3]) as db:
        db.row_factory=sqlite3.Row
        old=dict(db.execute('SELECT * FROM blog_posts WHERE slug=?',(data['slug'],)).fetchone());old.pop('layout');old.pop('toc_enabled')
        db.execute('ALTER TABLE blog_posts DROP COLUMN layout');db.execute('ALTER TABLE blog_posts DROP COLUMN toc_enabled')
    restarted=create_app({'TESTING':True,'SECRET_KEY':'upgraded','DATABASE':site[3]}).test_client()
    with sqlite3.connect(site[3]) as db:
        db.row_factory=sqlite3.Row;new=dict(db.execute('SELECT * FROM blog_posts WHERE slug=?',(data['slug'],)).fetchone())
    assert new.pop('layout')=='legacy' and new.pop('toc_enabled')==0 and new==old
    assert 'class="article-cover"' in restarted.get('/blog/'+data['slug']).text
