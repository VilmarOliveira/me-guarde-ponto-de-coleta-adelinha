"""Persistent icon catalog and shared server preview for the existing blog."""
import hashlib
import base64
import re
from io import BytesIO
from PIL import Image
from flask import Blueprint,Response,abort,flash,redirect,render_template,request,url_for
from markupsafe import escape
from blog_content import BUILTINS,ALIASES,render_article

ICON_MAX_BYTES=100*1024


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS blog_icon_assets (
        digest TEXT PRIMARY KEY,image BYTEA NOT NULL,mime TEXT NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS blog_icons (
        identifier TEXT PRIMARY KEY,name TEXT NOT NULL,asset_hash TEXT NOT NULL REFERENCES blog_icon_assets(digest),
        active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)''')


def catalog(db):
    entries={name:{'identifier':name,'name':label,'src':url_for('static',filename=f'blog-icons/{name}.svg'),'custom':False,'active':True} for name,(label,_) in BUILTINS.items()}
    for alias,name in ALIASES.items():entries[alias]=entries[name]
    for raw in db.execute('SELECT identifier,name,asset_hash,active FROM blog_icons ORDER BY name,identifier').fetchall():
        row=dict(raw);row.update(src=url_for('blog_tools.icon_image',digest=row['asset_hash']),archive_url=url_for('blog_tools.archive_icon',identifier=row['identifier']),custom=True)
        entries[row['identifier']]=row
    return entries


def editor_context(db,current_staff):
    icons=catalog(db)
    staff=current_staff()
    return {'blog_icons':[row for key,row in icons.items() if key not in ALIASES and row['active']],
            'known_blog_icons':list(icons),'can_manage_blog_icons':not staff or staff['role']=='administrador','blog_icon_max_bytes':ICON_MAX_BYTES}


def article_context(db,post,legacy_formatter):
    if dict(post).get('layout')=='icons':return {'article':render_article(post['body'],catalog(db)),'formatted_body':None}
    return {'article':None,'formatted_body':legacy_formatter(post['body'])}


def cover_data_uri(data):
    if not data:return None
    data=bytes(data)
    mime='image/png' if data.startswith(b'\x89PNG') else 'image/webp' if data.startswith(b'RIFF') else 'image/jpeg'
    return 'data:'+mime+';base64,'+base64.b64encode(data).decode()


def resolve_cover(db,form,files,validator):
    """One rule for preview/save: retain unless explicitly replaced or removed."""
    previous=db.execute('SELECT cover_image,cover_alt FROM blog_posts WHERE '+('id=?' if form.get('post_id') else 'slug=?'),(form.get('post_id') or form.get('slug','').strip().lower(),)).fetchone()
    upload=files.get('cover_image');has_upload=bool(upload and upload.filename)
    data=upload.read(900*1024+1) if has_upload else None
    if has_upload and not validator(data,900*1024):raise ValueError('A capa deve ser PNG, JPG ou WebP de até 900 KB. A imagem anterior foi preservada.')
    remove=form.get('remove_cover')=='yes'
    if has_upload and remove:raise ValueError('Escolha substituir ou remover a capa. Desmarque a remoção para enviar uma imagem. A imagem anterior foi preservada.')
    if remove:return None,''
    alt=form.get('cover_alt',previous['cover_alt'] if previous else '') or ''
    return (data if has_upload else bytes(previous['cover_image']) if previous and previous['cover_image'] else None),alt.strip()


def install(app,db,admin_required,current_staff,now,legacy_formatter,validator):
    bp=Blueprint('blog_tools',__name__)
    def icon_admin():
        staff=current_staff()
        if staff and staff['role']!='administrador':abort(403)
    def reply(message,status=200,entry=None):
        if request.headers.get('X-Requested-With')=='XMLHttpRequest':return {'message':message,'icon':entry},status
        flash(message,'success' if status==200 else 'error');return redirect(url_for('blog_admin'))

    @bp.get('/blog/icones/imagem/<digest>')
    def icon_image(digest):
        if not re.fullmatch('[a-f0-9]{64}',digest):abort(404)
        row=db().execute('SELECT image,mime FROM blog_icon_assets WHERE digest=?',(digest,)).fetchone()
        if not row:abort(404)
        response=Response(bytes(row['image']),mimetype=row['mime'],headers={'Cache-Control':'public,max-age=31536000,immutable','X-Content-Type-Options':'nosniff'})
        response.set_etag(digest)
        return response.make_conditional(request)

    @bp.post('/painel/blog/previa')
    @admin_required
    def preview():
        post={key:request.form.get(key,'') for key in ('title','summary','slug','cover_alt','seo_title','seo_description')}
        post.update(body=str(escape(request.form.get('body',''))),layout='icons' if request.form.get('layout','icons')=='icons' else 'legacy',toc_enabled=int(request.form.get('toc_enabled')=='yes'),cover_image=None,published_at=None,updated_at=None)
        try:data,alt=resolve_cover(db(),request.form,request.files,validator)
        except ValueError as error:return {'message':str(error)},400
        post.update(cover_image=bool(data),cover_alt=alt,preview_cover_src=cover_data_uri(data))
        context=article_context(db(),post,legacy_formatter)
        return {'html':render_template('blog_article.html',post=post,preview=True,**context),'warnings':context['article']['warnings'] if context['article'] else []}

    @bp.post('/painel/blog/icones')
    @admin_required
    def upload_icon():
        icon_admin()
        identifier=request.form.get('identifier','').strip().lower();name=request.form.get('name','').strip()
        if not re.fullmatch('[a-z][a-z0-9-]{0,47}',identifier) or identifier in BUILTINS or identifier in ALIASES:return reply('Use um identificador exclusivo, com até 48 letras minúsculas, números ou hífen. Os identificadores da biblioteca são reservados.',400)
        if not name or len(name)>100:return reply('Informe um nome com até 100 caracteres.',400)
        upload=request.files.get('image');data=upload.read(ICON_MAX_BYTES+1) if upload else None
        mime=validator(data,ICON_MAX_BYTES)
        if mime not in ('image/png','image/webp'):return reply('O ícone deve ser um PNG ou WebP válido de até 100 KB. A imagem anterior foi preservada.',400)
        try:
            with Image.open(BytesIO(data)) as image:
                if image.width*image.height>16_000_000:return reply('A imagem excede o limite técnico de 16 megapixels. A imagem anterior foi preservada.',400)
                image.load()
        except (OSError,ValueError,Image.DecompressionBombError):return reply('Não foi possível ler o ícone. A imagem anterior foi preservada.',400)
        digest=hashlib.sha256(data).hexdigest()
        db().execute('INSERT INTO blog_icon_assets(digest,image,mime) VALUES(?,?,?) ON CONFLICT DO NOTHING',(digest,data,mime))
        db().execute('''INSERT INTO blog_icons(identifier,name,asset_hash,active,updated_at) VALUES(?,?,?,1,?)
            ON CONFLICT(identifier) DO UPDATE SET name=excluded.name,asset_hash=excluded.asset_hash,active=1,updated_at=excluded.updated_at''',(identifier,name,digest,now().isoformat()));db().commit()
        return reply('Ícone salvo com sucesso!',entry=catalog(db())[identifier])

    @bp.post('/painel/blog/icones/<identifier>/remover')
    @admin_required
    def archive_icon(identifier):
        icon_admin()
        result=db().execute('UPDATE blog_icons SET active=0,updated_at=? WHERE identifier=?',(now().isoformat(),identifier));db().commit()
        if result.rowcount!=1:abort(404)
        return reply('Ícone removido da biblioteca. Os artigos e as versões anteriores da imagem continuam funcionando.')

    app.register_blueprint(bp)
