"""Explicitly published client requests; immutable versions and client responses."""
import hashlib
import hmac
import json
import secrets
import sqlite3
import psycopg
from database import DatabaseIntegrityError
from datetime import datetime
from core import TZ
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from client_compliance import compliance, signature_data, valid_email, FIELDS, archive_legacy, diagnostics, revision_id, notice_record_ids
from email_challenges import issue as issue_email, verify as verify_email

KINDS={'notice':'Aviso informativo','mandatory':'Regularização obrigatória','optional':'Autorização opcional'}
ACTIONS={'read':'Confirmar leitura','profile':'Atualizar dados','terms':'Aceitar termos','signature':'Assinar','email':'Confirmar por código de e-mail'}
FUNCTIONS={'delivery':'Dados para novas entregas','new_packages':'Registrar novas encomendas','plans':'Contratar ou renovar o plano'}
CRITERIA={'profile':'Dados do cadastro incompletos','signature':'Assinatura ausente ou inválida','email_confirmation':'Confirmação contratual por e-mail pendente','contract_version':'Versão do contrato ainda não aceita'}


def unpack(row):
    row=dict(row)
    for field in ('actions','functions','clients'):row[field]=json.loads(row[field+'_json'])
    row['document_revision_id']=revision_id(row['document_version'],row['body']) if row['is_contract'] else None
    return row


def accepted_contract(db,owner,version,text):
    history=[dict(row) for row in db.execute('SELECT * FROM client_contract_records WHERE client_id=? ORDER BY id DESC',(owner['id'],)).fetchall()]
    rejected=notice_record_ids(db,owner['id']) if history else set()
    candidates=[dict(owner)]+[row for row in history if row['id'] not in rejected]
    expected=revision_id(version,text)
    for row in candidates:
        if row.get('terms_version')!=version or row.get('terms_text')!=text:continue
        record_revision=row.get('contract_revision_id') or revision_id(row.get('terms_version'),row.get('terms_text'))
        if record_revision!=expected:continue
        # Existing evidence, not a new authorization inferred from login.
        from client_compliance import acceptance_key
        proof=db.execute('SELECT email,confirmed_at FROM client_contract_email_confirmations WHERE client_id=? AND acceptance_key=?',(owner['id'],acceptance_key(row))).fetchone()
        from client_compliance import valid_date
        confirmed=bool(proof and valid_email(proof['email']) and valid_date(proof['confirmed_at']))
        if not any(item['group'] in ('acceptance','confirmation') for item in diagnostics(owner,row,confirmed)):
            return row
    return None


def audience_matches(db,req,owner):
    if req['audience']=='all':return True
    if req['audience']=='specific':return owner['id'] in req['clients']
    _,missing=compliance(db,owner)
    criterion=req['criterion']
    if criterion=='contract_version':return not accepted_contract(db,owner,req['document_version'],req['body'])
    if criterion=='profile':return any(x['group']=='profile' for x in missing)
    return any(x['key']==criterion for x in missing)


def assign_audience(db,req,value):
    """Snapshot eligible existing clients without treating assignment as consent."""
    if req['starts_at']>value:return
    for person in db.execute('SELECT * FROM clients').fetchall():
        if audience_matches(db,req,person):
            db.execute('INSERT INTO client_action_assignments(request_id,client_id,assigned_at) VALUES(?,?,?) ON CONFLICT DO NOTHING',(req['id'],person['id'],value))


def current_contract(db,default):
    row=db.execute("SELECT document_version,document_label,body FROM client_action_requests WHERE is_contract=1 AND published_at IS NOT NULL AND starts_at<=? ORDER BY published_at DESC,id DESC LIMIT 1",(datetime.now(TZ).isoformat(),)).fetchone()
    return (row['document_version'],row['document_label'],row['body']) if row else default


def response_for(db,request_id,client_id):
    return db.execute('SELECT * FROM client_action_responses WHERE request_id=? AND client_id=? ORDER BY id DESC LIMIT 1',(request_id,client_id)).fetchone()


def client_items(db,owner,stamp,include_closed=False):
    rows=db.execute("SELECT * FROM client_action_requests WHERE status!='draft' ORDER BY id DESC").fetchall()
    items=[]
    for raw in rows:
        req=unpack(raw)
        assigned=db.execute('SELECT * FROM client_action_assignments WHERE request_id=? AND client_id=?',(req['id'],owner['id'])).fetchone()
        if not assigned and req['status']=='published' and req['starts_at']<=stamp and audience_matches(db,req,owner):
            db.execute('INSERT INTO client_action_assignments(request_id,client_id,assigned_at) VALUES(?,?,?) ON CONFLICT DO NOTHING',(req['id'],owner['id'],stamp))
            db.commit()
            assigned=db.execute('SELECT * FROM client_action_assignments WHERE request_id=? AND client_id=?',(req['id'],owner['id'])).fetchone()
        if not assigned:continue
        answer=response_for(db,req['id'],owner['id'])
        evidence=accepted_contract(db,owner,req['document_version'],req['body']) if req['is_contract'] and req['require_existing'] else None
        status=answer['status'] if answer else 'cancelled' if req['status']=='cancelled' else 'completed' if evidence else 'pending'
        # A response flag alone never proves a contractual acceptance. Require
        # the exact document, actual signature and confirmed email evidence.
        if req['is_contract'] and req['require_existing']:
            status=('accepted' if answer else 'completed') if evidence else 'cancelled' if req['status']=='cancelled' else 'pending'
        if req['starts_at']>stamp and not answer:continue
        req.update(assignment=dict(assigned),answer=dict(answer) if answer else None,state=status,evidence=evidence,overdue=bool(req['due_at'] and req['due_at']<stamp and status=='pending'))
        if include_closed or status=='pending':items.append(req)
    return items


def summary(db,owner,stamp):
    items=client_items(db,owner,stamp,True)
    record,missing=compliance(db,owner)
    pending=[item for item in items if item['state']=='pending']
    covered=set()
    for item in pending:
        if 'profile' in item['actions']:covered.add('profile')
        if item['is_contract'] and item['require_existing']:
            covered.update(('acceptance','confirmation'))
    unresolved=[item for item in missing if item['group'] not in covered]
    subjects={'profile' if item['group']=='profile' else 'contract' for item in unresolved}
    return items,unresolved,len(pending)+len(subjects)


def blockers(db,owner,stamp,include_baseline=False):
    _,missing=compliance(db,owner)
    result={name:[] for name in FUNCTIONS}
    if missing and include_baseline:
        for name in result:result[name].append('Regularizar os dados e as evidências do cadastro')
    for req in client_items(db,owner,stamp):
        if req['kind']=='mandatory' and req['status']=='published':
            for name in req['functions']:result[name].append(req['title'])
    return result


def schema(connection,id_type):
    connection.execute(f'''CREATE TABLE IF NOT EXISTS client_action_requests (
        id {id_type},family_key TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'draft',
        title TEXT NOT NULL,explanation TEXT NOT NULL,body TEXT NOT NULL,kind TEXT NOT NULL,audience TEXT NOT NULL,
        clients_json TEXT NOT NULL,criterion TEXT,actions_json TEXT NOT NULL,functions_json TEXT NOT NULL,
        starts_at TEXT NOT NULL,due_at TEXT,is_contract INTEGER NOT NULL DEFAULT 0,require_existing INTEGER NOT NULL DEFAULT 0,
        document_version TEXT,document_label TEXT,content_hash TEXT NOT NULL,created_at TEXT NOT NULL,created_by TEXT NOT NULL,
        published_at TEXT,cancelled_at TEXT,cancel_reason TEXT, UNIQUE(family_key,version))''')
    connection.execute(f'''CREATE TABLE IF NOT EXISTS client_action_assignments (
        id {id_type}, request_id BIGINT NOT NULL REFERENCES client_action_requests(id),client_id BIGINT NOT NULL REFERENCES clients(id),
        assigned_at TEXT NOT NULL,UNIQUE(request_id,client_id))''')
    connection.execute(f'''CREATE TABLE IF NOT EXISTS client_action_responses (
        id {id_type},request_id BIGINT NOT NULL REFERENCES client_action_requests(id),client_id BIGINT NOT NULL REFERENCES clients(id),
        request_version INTEGER NOT NULL,title TEXT NOT NULL,explanation TEXT NOT NULL,body TEXT NOT NULL,content_hash TEXT NOT NULL,
        actions_json TEXT NOT NULL,response TEXT NOT NULL,status TEXT NOT NULL,responded_at TEXT NOT NULL,
        signature TEXT,email TEXT,email_confirmed_at TEXT,contract_record_id BIGINT REFERENCES client_contract_records(id),
        profile_audit_id BIGINT,accept_ip TEXT,accept_user_agent TEXT)''')
    connection.execute('CREATE TABLE IF NOT EXISTS client_published_contract_versions(version TEXT PRIMARY KEY,body TEXT NOT NULL,request_id BIGINT NOT NULL REFERENCES client_action_requests(id))')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_client_actions_owner ON client_action_assignments(client_id,request_id)')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_client_action_responses_owner ON client_action_responses(request_id,client_id,id)')


def contract_collision(db,version,text,default):
    for table in ('clients','client_contract_records'):
        if db.execute(f'SELECT 1 FROM {table} WHERE terms_version=? AND terms_text IS NOT NULL AND terms_text!=?',(version,text)).fetchone():return True
    if version==default[0] and text!=default[2]:return True
    return bool(db.execute("SELECT 1 FROM client_action_requests WHERE published_at IS NOT NULL AND is_contract=1 AND document_version=? AND body!=?",(version,text)).fetchone())


def prepare_current_reacceptance(db,default,value,explanation,actor,expected_revision):
    """Explicit admin/CLI operation; never invoked automatically by startup/login."""
    explanation=explanation.strip()
    if not explanation or len(explanation)>2000:raise ValueError('Informe um resumo das mudanças com até 2000 caracteres.')
    db.commit();db.begin_write()
    version,label,text=current_contract(db,default)
    if revision_id(version,text)!=expected_revision:
        db.rollback();raise ValueError('O contrato vigente mudou. Confira o novo texto antes de exigir o aceite.')
    source=db.execute("SELECT * FROM client_action_requests WHERE is_contract=1 AND published_at IS NOT NULL AND document_version=? AND body=? ORDER BY published_at DESC,id DESC LIMIT 1",(version,text)).fetchone()
    family=source['family_key'] if source else 'reaceite-'+expected_revision
    db.execute('SELECT id FROM client_action_requests WHERE family_key=? AND version=1'+(' FOR UPDATE' if db.postgres else ''),(family,)).fetchone()
    existing=db.execute("SELECT id FROM client_action_requests WHERE family_key=? AND document_version=? AND body=? AND require_existing=1 AND status IN ('draft','published') ORDER BY id DESC LIMIT 1",(family,version,text)).fetchone()
    if existing:db.commit();return existing['id']
    number=db.execute('SELECT COALESCE(MAX(version),0)+1 n FROM client_action_requests WHERE family_key=?',(family,)).fetchone()['n']
    data=dict(title='Novo aceite do contrato vigente',explanation=explanation,body=text,kind='mandatory',audience='criterion',
        clients_json='[]',criterion='contract_version',actions_json=json.dumps(['email','signature','terms']),functions_json='[]',
        starts_at=value,due_at=None,is_contract=1,require_existing=1,document_version=version,document_label=label)
    data['content_hash']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    row=db.execute('INSERT INTO client_action_requests(family_key,version,status,created_at,created_by,'+','.join(data)+') VALUES(?,?,?,?,?,'+','.join('?' for _ in data)+') ON CONFLICT(family_key,version) DO NOTHING RETURNING id',
        (family,number,'draft',value,actor,*data.values())).fetchone()
    if not row:row=db.execute('SELECT id FROM client_action_requests WHERE family_key=? AND version=?',(family,number)).fetchone()
    db.commit();return row['id']


def publish_draft(db,req_id,expected_hash,value,default):
    """Shared explicit publication for the reviewed admin form and deployment CLI."""
    db.commit();db.begin_write()
    family=db.execute('SELECT family_key FROM client_action_requests WHERE id=?',(req_id,)).fetchone()
    if not family:db.rollback();raise LookupError('Solicitação não encontrada.')
    db.execute('SELECT id FROM client_action_requests WHERE family_key=? AND version=1'+(' FOR UPDATE' if db.postgres else ''),(family['family_key'],)).fetchone()
    row=db.execute('SELECT * FROM client_action_requests WHERE id=?'+(' FOR UPDATE' if db.postgres else ''),(req_id,)).fetchone()
    item=unpack(row)
    if item['status']=='published':db.commit();return
    if item['status']!='draft' or expected_hash!=item['content_hash']:db.rollback();raise RuntimeError('Prévia desatualizada ou solicitação indisponível.')
    if item['is_contract'] and contract_collision(db,item['document_version'],item['body'],default):
        db.rollback();raise ValueError('Conflito de versão contratual. Revise o código técnico; o histórico não foi alterado.')
    duplicate=db.execute('SELECT id FROM client_action_requests WHERE family_key=? AND published_at IS NOT NULL AND content_hash=?',(item['family_key'],item['content_hash'])).fetchone()
    if duplicate:db.rollback();raise ValueError('A mesma versão de conteúdo já foi publicada. Edite o rascunho antes de publicar novamente.')
    if item['is_contract']:
        db.execute('INSERT INTO client_published_contract_versions(version,body,request_id) VALUES(?,?,?) ON CONFLICT DO NOTHING',(item['document_version'],item['body'],req_id))
        registered=db.execute('SELECT v.body,r.family_key FROM client_published_contract_versions v JOIN client_action_requests r ON r.id=v.request_id WHERE v.version=?',(item['document_version'],)).fetchone()
        if registered['body']!=item['body'] or registered['family_key']!=item['family_key']:
            db.rollback();raise ValueError('Essa versão contratual já foi publicada. Use a solicitação original ou um novo código técnico.')
    assign_audience(db,item,value)
    db.execute("UPDATE client_action_requests SET status='cancelled',cancelled_at=?,cancel_reason=? WHERE family_key=? AND status='published'",(value,f'Substituída pela versão {item["version"]}',item['family_key']))
    db.execute("UPDATE client_action_requests SET status='published',published_at=? WHERE id=?",(value,req_id));db.commit()


def install(app,db,client_required,admin_required,current_staff,now,send_code,default_contract):
    bp=Blueprint('client_actions',__name__)
    def stamp():return now().isoformat()
    def owner():return db().execute('SELECT * FROM clients WHERE id=?',(session['client_id'],)).fetchone()
    def admin():
        staff=current_staff()
        if staff and staff['role']!='administrador':abort(403)
    def get(req_id):
        row=db().execute('SELECT * FROM client_action_requests WHERE id=?',(req_id,)).fetchone()
        if not row:abort(404)
        return unpack(row)
    def parse(previous=None):
        data={key:request.form.get(key,'').strip() for key in ('title','explanation','body','kind','audience','criterion','document_version','document_label')}
        if not data['title'] or len(data['title'])>140 or not data['explanation'] or len(data['explanation'])>2000 or not data['body'] or len(data['body'])>50000:raise ValueError('Informe título, explicação e texto completo dentro dos limites indicados.')
        if data['kind'] not in KINDS or data['audience'] not in ('all','specific','criterion'):raise ValueError('Tipo ou público inválido.')
        data['is_contract']=int(request.form.get('is_contract')=='yes');data['require_existing']=int(data['is_contract'] and request.form.get('require_existing')=='yes')
        data['actions']=sorted(set(request.form.getlist('actions')))
        data['functions']=sorted(set(request.form.getlist('functions')))
        if not data['actions'] or any(x not in ACTIONS for x in data['actions']) or any(x not in FUNCTIONS for x in data['functions']):raise ValueError('Selecione ações e funções válidas.')
        if 'profile' in data['actions'] and (data['is_contract'] or len(data['actions'])>1):raise ValueError('Separe os assuntos: publique a atualização de dados em uma solicitação própria, sem assinatura, termos ou outras autorizações.')
        if data['is_contract']:
            if not data['document_version'] or len(data['document_version'])>100:raise ValueError('Informe um código técnico exclusivo do contrato.')
            data['document_label']=data['document_label'] or data['title']
            if contract_collision(db(),data['document_version'],data['body'],default_contract()):raise ValueError('Essa versão já identifica outro texto. Escolha um código técnico novo; o histórico será preservado.')
            if data['require_existing']:
                data.update(kind='mandatory',audience='criterion',criterion='contract_version',actions=['email','signature','terms'])
            else:data.update(kind='notice',actions=['read'],functions=[])
        if data['kind']!='mandatory':data['functions']=[]
        if data['kind']=='notice' and data['actions']!=['read']:raise ValueError('Avisos informativos usam somente confirmação de leitura e nunca bloqueiam funções.')
        if len(data['document_label'])>140:raise ValueError('O título do contrato deve ter até 140 caracteres.')
        if data['audience']=='criterion' and data['criterion'] not in CRITERIA:raise ValueError('Selecione o requisito do público.')
        if data['criterion']=='contract_version' and not data['is_contract']:raise ValueError('A seleção por versão exige documento contratual e código técnico.')
        data['clients']=[]
        if data['audience']=='specific':
            tokens=request.form.get('client_codes','').replace(',',' ').split()
            if not tokens or len(tokens)>500:raise ValueError('Informe até 500 IDs públicos dos clientes.')
            for token in dict.fromkeys(tokens):
                row=db().execute('SELECT id FROM clients WHERE public_id=?',(token,)).fetchone()
                if not row:raise ValueError('Um dos IDs públicos informados não foi encontrado.')
                data['clients'].append(row['id'])
        for name in ('starts_at','due_at'):
            value=request.form.get(name,'').strip()
            if not value and name=='starts_at':value=stamp()
            try:
                parsed=datetime.fromisoformat(value) if value else None
                if parsed:parsed=parsed.astimezone(now().tzinfo) if parsed.tzinfo else parsed.replace(tzinfo=now().tzinfo)
                data[name]=previous[name] if previous and previous.get(name) and value==previous[name][:16] else parsed.isoformat() if parsed else None
            except ValueError:raise ValueError('Informe datas válidas.')
        if data['due_at'] and data['due_at']<data['starts_at']:raise ValueError('O prazo não pode anteceder o início.')
        for name in ('actions','functions','clients'):data[name+'_json']=json.dumps(data.pop(name))
        data['content_hash']=hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return data

    @bp.route('/painel/configuracoes/pendencias',methods=['GET','POST'])
    @admin_required
    def admin_hub():
        admin()
        if request.method=='POST':
            try:data=parse()
            except ValueError as e:flash(str(e),'error');return render_template('actions_admin_edit.html',item={},form=request.form,**choices()),400
            columns=','.join(data);marks=','.join('?' for _ in data)
            row=db().execute(f'INSERT INTO client_action_requests(family_key,version,status,created_at,created_by,{columns}) VALUES(?,1,?,?,?,{marks}) RETURNING id',
                (secrets.token_hex(16),'draft',stamp(),str(session.get('staff_user_id') or 'administrador-legado'),*data.values())).fetchone();db().commit()
            return redirect(url_for('client_actions.preview',req_id=row['id']))
        rows=[unpack(r) for r in db().execute('SELECT * FROM client_action_requests ORDER BY id DESC').fetchall()]
        version,label,text=current_contract(db(),default_contract())
        affected=sum(not accepted_contract(db(),person,version,text) for person in db().execute('SELECT * FROM clients WHERE active=1').fetchall())
        return render_template('actions_admin.html',items=rows,current_document={'version':version,'label':label,'text':text,'revision':revision_id(version,text)},affected=affected,**choices())
    def choices():return {'kinds':KINDS,'actions':ACTIONS,'functions':FUNCTIONS,'criteria':CRITERIA,'signature_data':signature_data}

    @bp.post('/painel/configuracoes/pendencias/contrato-vigente/exigir-aceite')
    @admin_required
    def require_current():
        admin()
        if request.form.get('require_existing')!='yes':abort(400)
        try:
            rid=prepare_current_reacceptance(db(),default_contract(),stamp(),request.form.get('summary',''),str(session.get('staff_user_id') or 'administrador-legado'),request.form.get('expected_revision'))
        except ValueError as error:
            flash(str(error),'error');return redirect(url_for('client_actions.admin_hub'))
        entry=get(rid)
        flash('Exigência do contrato vigente preparada. Confira a prévia e publique para gerar as pendências.' if entry['status']=='draft' else 'Esta revisão já possui uma exigência publicada; nenhuma duplicidade foi criada.','success')
        return redirect(url_for('client_actions.preview' if entry['status']=='draft' else 'client_actions.tracking',req_id=rid))

    @bp.route('/painel/configuracoes/pendencias/<int:req_id>',methods=['GET','POST'])
    @admin_required
    def admin_edit(req_id):
        admin();item=get(req_id)
        if request.method=='POST':
            if item['status']!='draft':abort(409)
            try:data=parse(item)
            except ValueError as e:flash(str(e),'error');return render_template('actions_admin_edit.html',item=item,form=request.form,**choices()),400
            result=db().execute('UPDATE client_action_requests SET '+','.join(key+'=?' for key in data)+' WHERE id=? AND status=?',(*data.values(),req_id,'draft'))
            db().commit()
            if result.rowcount!=1:abort(409)
            return redirect(url_for('client_actions.preview',req_id=req_id))
        codes=[db().execute('SELECT public_id FROM clients WHERE id=?',(cid,)).fetchone() for cid in item['clients']]
        return render_template('actions_admin_edit.html',item=item,form=None,client_codes=' '.join(row['public_id'] for row in codes if row),**choices())

    @bp.get('/painel/configuracoes/pendencias/<int:req_id>/previa')
    @admin_required
    def preview(req_id):
        admin();item=get(req_id)
        people=[db().execute('SELECT public_id,name FROM clients WHERE id=?',(cid,)).fetchone() for cid in item['clients']]
        return render_template('actions_preview.html',item=item,people=people,**choices())

    @bp.post('/painel/configuracoes/pendencias/<int:req_id>/publicar')
    @admin_required
    def publish(req_id):
        admin()
        if request.form.get('confirm')!='yes':abort(409)
        try:publish_draft(db(),req_id,request.form.get('content_hash'),stamp(),default_contract())
        except LookupError:abort(404)
        except RuntimeError:abort(409)
        except ValueError as error:
            flash(str(error),'error');return redirect(url_for('client_actions.admin_edit',req_id=req_id))
        flash('Solicitação publicada. Nenhuma resposta foi registrada em nome dos clientes.','success');return redirect(url_for('client_actions.tracking',req_id=req_id))

    @bp.post('/painel/configuracoes/pendencias/<int:req_id>/nova-versao')
    @admin_required
    def clone(req_id):
        admin();item=get(req_id);db().commit();db().begin_write()
        db().execute('SELECT id FROM client_action_requests WHERE family_key=? AND version=1'+(' FOR UPDATE' if db().postgres else ''),(item['family_key'],)).fetchone()
        version=db().execute('SELECT MAX(version) n FROM client_action_requests WHERE family_key=?',(item['family_key'],)).fetchone()['n']+1
        fields=('title','explanation','body','kind','audience','clients_json','criterion','actions_json','functions_json','starts_at','due_at','is_contract','require_existing','document_version','document_label','content_hash')
        row=db().execute('INSERT INTO client_action_requests(family_key,version,status,created_at,created_by,'+','.join(fields)+') VALUES(?,?,?,?,?,'+','.join('?' for _ in fields)+') RETURNING id',
            (item['family_key'],version,'draft',stamp(),str(session.get('staff_user_id') or 'administrador-legado'),*[item[f] for f in fields])).fetchone();db().commit()
        return redirect(url_for('client_actions.admin_edit',req_id=row['id']))

    @bp.post('/painel/configuracoes/pendencias/<int:req_id>/cancelar')
    @admin_required
    def cancel(req_id):
        admin();db().commit();db().begin_write()
        row=db().execute('SELECT * FROM client_action_requests WHERE id=?'+(' FOR UPDATE' if db().postgres else ''),(req_id,)).fetchone()
        if not row:abort(404)
        item=unpack(row);value=stamp()
        if item['status']=='published':
            assign_audience(db(),item,value)
            db().execute("UPDATE client_action_requests SET status='cancelled',cancelled_at=?,cancel_reason=? WHERE id=?",(value,request.form.get('reason','Cancelada pelo administrador')[:500],req_id))
        db().commit()
        return redirect(url_for('client_actions.tracking',req_id=req_id))

    @bp.get('/painel/configuracoes/pendencias/<int:req_id>/acompanhamento')
    @admin_required
    def tracking(req_id):
        admin();item=get(req_id);states=[]
        query=request.args.get('client','').strip()
        owners=db().execute('SELECT * FROM clients'+(' WHERE public_id=?' if query else '')+' ORDER BY id',(query,) if query else ()).fetchall()
        for person in owners:
            entry=next((x for x in client_items(db(),person,stamp(),True) if x['id']==req_id),None)
            if entry:states.append({'client':person,'item':entry})
        responses=db().execute('SELECT * FROM client_action_responses WHERE request_id=?'+(' AND client_id IN (SELECT id FROM clients WHERE public_id=?)' if query else '')+' ORDER BY id',(req_id,query) if query else (req_id,)).fetchall()
        return render_template('actions_tracking.html',item=item,states=states,responses=responses,**choices())

    @bp.get('/cliente/pendencias-e-autorizacoes')
    @client_required
    def hub():
        person=owner();items,missing,_=summary(db(),person,stamp())
        return render_template('actions_client.html',items=items,missing=missing,**choices())

    def client_request(req_id):
        person=owner();entry=next((x for x in client_items(db(),person,stamp(),True) if x['id']==req_id),None)
        if not entry:abort(404)
        return person,entry
    def token(person,item):
        answer=item['answer']['id'] if item['answer'] else 0
        payload=f"{item['id']}:{item['content_hash']}:{person['id']}:{person['session_version']}:{person['email']}:{answer}"
        return hmac.new(app.secret_key.encode(),payload.encode(),hashlib.sha256).hexdigest()
    def dependency(person,item):
        if not (item['is_contract'] and item['require_existing']):return None
        missing=[x for x in compliance(db(),person)[1] if x['group']=='profile']
        if not missing:return None
        pending=next((req for req in client_items(db(),person,stamp()) if req['id']!=item['id'] and req['actions']==['profile']),None)
        return {'title':pending['title'] if pending else 'Dados cadastrais','missing':missing,
                'href':url_for('client_actions.resolve',req_id=pending['id']) if pending else url_for('regularization.profile')}
    def render(person,item,status=200):
        return render_template('actions_resolve.html',item=item,client=person,scope=token(person,item),dependency=dependency(person,item),profile_missing=any(x['group']=='profile' for x in compliance(db(),person)[1]),submitted_signature=signature_data(request.form.get('signature','')) if request.method=='POST' else None,confirmed=request.method=='POST' and request.form.get('confirmed')=='yes',responses=db().execute('SELECT * FROM client_action_responses WHERE request_id=? AND client_id=? ORDER BY id',(item['id'],person['id'])).fetchall(),**choices()),status

    @bp.route('/cliente/pendencias-e-autorizacoes/<int:req_id>',methods=['GET','POST'])
    @client_required
    def resolve(req_id):
        try:return resolve_request(req_id)
        except (DatabaseIntegrityError,sqlite3.DatabaseError,psycopg.Error):
            db().rollback()
            if request.method!='POST':raise
            person,item=client_request(req_id)
            flash('Não foi possível salvar os dados. Nenhum aceite foi concluído. Tente novamente.','error')
            return render(person,item,503)

    def resolve_request(req_id):
        person,item=client_request(req_id)
        if request.method=='GET':return render(person,item)
        if session.get('staff'):abort(403)
        if item['status']!='published' or item['state'] in ('completed','cancelled'):abort(409)
        choice='send_code' if request.form.get('email_intent')=='send_code' else request.form.get('choice','')
        if item['answer'] and not (item['is_contract'] and item['require_existing'] and item['state']=='pending') and not (item['kind']=='optional' and item['state']=='accepted' and choice=='revoke'):return redirect(url_for('client_actions.resolve',req_id=req_id))
        if request.form.get('scope')!=token(person,item):flash('A solicitação ou o cadastro mudou. Recarregue antes de responder.','error');return render(person,item,409)
        refusal=item['kind']=='optional' and choice in ('decline','revoke')
        if choice in ('send_code','accept') and dependency(person,item):
            flash('Resolva primeiro a pendência de dados cadastrais indicada abaixo.','error');return render(person,item,400)
        if choice=='send_code':
            if 'email' not in item['actions'] or not valid_email(person['email']):flash('Atualize seu e-mail antes de solicitar o código.','error');return render(person,item,400)
            status,message=issue_email(db(),app.secret_key,person,token(person,item),send_code)
            if request.headers.get('X-Requested-With')=='XMLHttpRequest':return {'sent':status==200,'message':message},status
            flash(message,'success' if status==200 else 'error');return render(person,item,status)
        if not refusal:
            if choice!='accept' or request.form.get('confirmed')!='yes':flash('Confirme expressamente a ação indicada.','error');return render(person,item,400)
            if 'profile' in item['actions']:
                audit=db().execute("SELECT id FROM audit_logs WHERE entity_type='cliente' AND entity_id=? AND action='editar_proprio_cadastro' AND created_at>=? ORDER BY id DESC LIMIT 1",(person['id'],max(item['published_at'],item['starts_at'],item['assignment']['assigned_at']))).fetchone()
                if not audit or any(x['group']=='profile' for x in compliance(db(),person)[1]):flash('Revise e salve seus dados antes de concluir.','error');return render(person,item,400)
            if 'signature' in item['actions'] and not signature_data(request.form.get('signature','')):flash('Assine no espaço indicado.','error');return render(person,item,400)
            if item['is_contract'] and item['require_existing'] and any(x['group']=='profile' for x in compliance(db(),person)[1]):flash('Complete os dados do cadastro antes do novo aceite.','error');return render(person,item,400)
        db().commit();db().begin_write()
        # Serialize cancellation/version publication and duplicate client submits.
        locked=db().execute('SELECT status FROM client_action_requests WHERE id=?'+(' FOR UPDATE' if db().postgres else ''),(req_id,)).fetchone()
        db().execute('SELECT id FROM client_action_assignments WHERE request_id=? AND client_id=?'+(' FOR UPDATE' if db().postgres else ''),(req_id,person['id'])).fetchone()
        fresh=owner();latest=response_for(db(),req_id,person['id'])
        if locked['status']!='published' or fresh['session_version']!=person['session_version'] or fresh['email']!=person['email'] or (latest['id'] if latest else 0)!=(item['answer']['id'] if item['answer'] else 0):
            db().rollback();return redirect(url_for('client_actions.resolve',req_id=req_id))
        if not refusal and ('profile' in item['actions'] or item['is_contract']) and any(x['group']=='profile' for x in compliance(db(),fresh)[1]):
            db().rollback();flash('Seus dados mudaram. Confira o cadastro antes de concluir.','error');return render(person,item,409)
        email_at=None;value=stamp();record_id=None;profile_audit=None
        if not refusal and 'email' in item['actions']:
            if not verify_email(db(),app.secret_key,fresh,token(person,item),request.form.get('code','')):
                db().commit();flash('Código inválido ou expirado. Nenhuma autorização foi registrada.','error');return render(person,item,400)
            email_at=value
        signature=request.form.get('signature','') if not refusal and 'signature' in item['actions'] else None
        if not refusal and 'profile' in item['actions']:profile_audit=audit['id']
        if item['is_contract'] and item['require_existing'] and not refusal:
            archive_legacy(db(),fresh,value)
            fields={key:None for key in FIELDS}
            fields.update(public_id=fresh['public_id'],contract_public_id=fresh['public_id'],name=fresh['name'],cpf=fresh['cpf'],terms_version=item['document_version'],terms_text=item['body'],terms_display_label=item['document_label'],contract_revision_id=item['document_revision_id'],signature=signature,accepted_at=value,confirmation_channel='email',confirmation_destination=fresh['email'],confirmation_validated_at=value,document_brand_version=app.config['CURRENT_DOCUMENT_BRAND'],document_logo_present=app.config['CURRENT_DOCUMENT_LOGO'],accept_ip=request.remote_addr,accept_user_agent=request.headers.get('User-Agent','')[:500])
            fields['contract_hash']=hashlib.sha256(json.dumps(fields,sort_keys=True,ensure_ascii=False).encode()).hexdigest();fields['contract_id']='ACEITE-'+secrets.token_hex(12)
            record_id=db().execute('INSERT INTO client_contract_records(client_id,source,recorded_at,'+','.join(FIELDS)+') VALUES(?,?,?,'+','.join('?' for _ in FIELDS)+') RETURNING id',(fresh['id'],'acceptance',value,*[fields[k] for k in FIELDS])).fetchone()['id']
        state='declined' if refusal else 'accepted' if item['kind']=='optional' or 'terms' in item['actions'] else 'completed'
        db().execute('''INSERT INTO client_action_responses(request_id,client_id,request_version,title,explanation,body,content_hash,actions_json,response,status,responded_at,signature,email,email_confirmed_at,contract_record_id,profile_audit_id,accept_ip,accept_user_agent)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(req_id,person['id'],item['version'],item['title'],item['explanation'],item['body'],item['content_hash'],item['actions_json'],choice,state,value,signature,fresh['email'] if email_at else None,email_at,record_id,profile_audit,request.remote_addr,request.headers.get('User-Agent','')[:500]))
        if email_at:db().execute('DELETE FROM client_regularization_codes WHERE client_id=?',(person['id'],))
        db().commit();flash('Dados atualizados com sucesso.' if not refusal else 'Resposta registrada. O histórico foi preservado.','success');return redirect(url_for('client_actions.hub'))

    app.register_blueprint(bp)
