"""Client-owned regularization, verified email codes and append-only evidence."""
import hashlib
import hmac
import json
import re
import secrets
import time
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from client_compliance import (FIELDS, compliance, archive_legacy, acceptance_key, records,
                               signature_data, valid_email)
from core import valid_cpf


def install_regularization(app,db,login_required,now,send_code,address_form,address_valid,current_contract):
    bp=Blueprint('regularization',__name__,url_prefix='/cliente')
    def client():return db().execute('SELECT * FROM clients WHERE id=?',(session['client_id'],)).fetchone()
    def state(owner):
        record,missing=compliance(db(),owner)
        needs_accept=any(x['group']=='acceptance' for x in missing)
        version,label,text=current_contract()
        contract={'terms_version':version,'terms_text':text,'terms_display_label':label} if needs_accept else record
        scope=hashlib.sha256(json.dumps({'client':owner['id'],'email':owner['email'],'name':owner['name'],'cpf':owner['cpf'],
            'action':'accept' if needs_accept else 'confirm','key':acceptance_key(record) if not needs_accept else None,
            'version':contract['terms_version'],'text':contract['terms_text']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        return record,missing,needs_accept,contract,scope
    def code_hash(owner,scope,code):
        return hmac.new(app.secret_key.encode(),f"{owner['id']}:{scope}:{code}".encode(),hashlib.sha256).hexdigest()
    def show(owner,status=200):
        record,missing,needs_accept,contract,scope=state(owner)
        return render_template('client_pending.html',client=owner,missing=missing,needs_accept=needs_accept,
            contract=contract,scope=scope,record=record,signature_preview=signature_data(record.get('signature'))),status

    @bp.route('/pendencias',methods=['GET','POST'])
    @login_required
    def pending():
        owner=client()
        if request.method=='GET':return show(owner)
        action=request.form.get('action','')
        if action=='profile':
            name=owner['name'] if len((owner['name'] or '').strip())>=5 else request.form.get('name','').strip()
            cpf=owner['cpf'] if valid_cpf(owner['cpf']) else ''.join(c for c in request.form.get('cpf','') if c.isdigit())
            phone=''.join(c for c in request.form.get('phone','') if c.isdigit())
            email=request.form.get('email','').strip().lower()
            address=address_form()
            if len(name)<5 or not valid_cpf(cpf) or len(phone)<10 or not valid_email(email) or not address_valid(address):
                flash('Confira nome, CPF, WhatsApp, e-mail e todos os campos obrigatórios do endereço.','error');return show(owner,400)
            if db().execute("SELECT 1 FROM clients WHERE REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','')=? AND id!=?",(cpf,owner['id'])).fetchone():
                flash('Este CPF já está em uso. Procure a equipe para conferir o cadastro.','error');return show(owner,400)
            password=owner['password_hash']
            if not password:
                value=request.form.get('password','')
                if len(value)<8 or value!=request.form.get('password_confirm'):
                    flash('Defina e confirme uma senha com pelo menos 8 caracteres.','error');return show(owner,400)
                password=generate_password_hash(value)
            archive_legacy(db(),owner,now().isoformat())
            db().execute('''UPDATE clients SET name=?,cpf=?,phone=?,email=?,password_hash=?,residential_cep=?,residential_street=?,
                residential_number=?,residential_district=?,residential_city=?,residential_state=?,residential_complement=? WHERE id=?''',
                (name,cpf,phone,email,password,''.join(c for c in address['residential_cep'] if c.isdigit()),address['residential_street'],address['residential_number'],
                 address['residential_district'],address['residential_city'],address['residential_state'].upper(),address['residential_complement'] or None,owner['id']))
            db().execute('DELETE FROM client_regularization_codes WHERE client_id=?',(owner['id'],));db().commit()
            flash('Dados atualizados. Confira as pendências restantes abaixo.','success');return redirect(url_for('regularization.pending'))
        record,missing,needs_accept,contract,scope=state(owner)
        if any(x['group']=='profile' for x in missing):
            flash('Complete primeiro os dados do cadastro.','error');return show(owner,400)
        if not missing:
            flash('Seu cadastro já está regularizado.','success');return redirect(url_for('regularization.pending'))
        if request.form.get('scope')!=scope:
            flash('Os dados ou o contrato mudaram. Recarregue e solicite um novo código.','error');return show(owner,409)
        if action=='send_code':
            stamp=int(time.time())
            previous=db().execute('SELECT * FROM client_regularization_codes WHERE client_id=?',(owner['id'],)).fetchone()
            if previous and stamp-previous['sent_at']<60:
                flash('Aguarde 60 segundos antes de reenviar o código.','error');return show(owner,429)
            sends=previous['sends'] if previous and stamp-previous['window_start']<900 else 0
            if sends>=3:
                flash('Limite de envios atingido. Aguarde 15 minutos.','error');return show(owner,429)
            # Reserve the rate-limit slot before calling the email provider.
            code=f'{secrets.randbelow(1000000):06d}'
            window=previous['window_start'] if sends else stamp
            reserved=db().execute('''INSERT INTO client_regularization_codes(client_id,scope_hash,code_hash,expires_at,attempts,sent_at,sends,window_start)
                VALUES(?,?,?,?,0,?,?,?) ON CONFLICT(client_id) DO UPDATE SET scope_hash=excluded.scope_hash,code_hash=excluded.code_hash,
                expires_at=excluded.expires_at,attempts=0,sent_at=excluded.sent_at,sends=excluded.sends,window_start=excluded.window_start
                WHERE client_regularization_codes.sent_at<=? AND (client_regularization_codes.sends<3 OR client_regularization_codes.window_start<=?) RETURNING client_id''',
                (owner['id'],scope,code_hash(owner,scope,code),stamp+600,stamp,sends+1,window,stamp-60,stamp-900)).fetchone();db().commit()
            if not reserved:
                flash('Aguarde antes de solicitar outro código.','error');return show(owner,429)
            try:sent,_=send_code('email',owner['email'],code)
            except Exception:sent=False
            if not sent:
                db().execute('UPDATE client_regularization_codes SET expires_at=0 WHERE client_id=?',(owner['id'],));db().commit()
                flash('Envio de e-mail indisponível. Nenhum aceite ou confirmação foi registrado. Tente novamente mais tarde.','error');return show(owner,503)
            flash('Código enviado ao seu e-mail. Ele expira em 10 minutos.','success');return show(owner)
        if action!='confirm':abort(400)
        if request.form.get('terms_accept')!='yes':
            flash('Confirme que leu o contrato apresentado.','error');return show(owner,400)
        signature=request.form.get('signature','') if needs_accept else None
        if needs_accept and not signature_data(signature):
            flash('Assine o contrato no espaço indicado.','error');return show(owner,400)
        db().commit()
        db().begin_write()
        fresh=db().execute('SELECT * FROM clients WHERE id=?'+(' FOR UPDATE' if db().postgres else ''),(owner['id'],)).fetchone()
        new_record,new_missing,new_accept,new_contract,new_scope=state(fresh)
        proof=db().execute('''UPDATE client_regularization_codes SET attempts=attempts+1 WHERE client_id=? AND scope_hash=?
            AND expires_at>? AND attempts<5 RETURNING *''',(owner['id'],scope,int(time.time()))).fetchone()
        supplied=request.form.get('code','')
        valid=proof and bool(re.fullmatch('[0-9]{6}',supplied)) and secrets.compare_digest(proof['code_hash'],code_hash(owner,scope,supplied))
        if not valid or new_scope!=scope or new_accept!=needs_accept or any(x['group']=='profile' for x in new_missing):
            db().commit();flash('Código inválido ou expirado, ou cadastro alterado. Confira e solicite outro código quando necessário.','error');return show(fresh,400)
        stamp=now().isoformat()
        archive_legacy(db(),fresh,stamp)
        if needs_accept:
            values={key:None for key in FIELDS}
            values.update(public_id=fresh['public_id'],contract_public_id=fresh['public_id'],name=fresh['name'],cpf=fresh['cpf'],
                terms_version=contract['terms_version'],terms_text=contract['terms_text'],terms_display_label=contract.get('terms_display_label'),
                signature=signature,accepted_at=stamp,confirmation_channel='email',confirmation_destination=fresh['email'],
                confirmation_validated_at=stamp,document_brand_version=app.config['CURRENT_DOCUMENT_BRAND'],document_logo_present=app.config['CURRENT_DOCUMENT_LOGO'],
                accept_ip=request.remote_addr,accept_user_agent=request.headers.get('User-Agent','')[:500])
            values['contract_hash']=hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            values['contract_id']='ACEITE-'+secrets.token_hex(12)
            columns=','.join(FIELDS);placeholders=','.join('?' for _ in FIELDS)
            db().execute(f'INSERT INTO client_contract_records(client_id,source,recorded_at,{columns}) VALUES(?,?,?,{placeholders})',
                (owner['id'],'acceptance',stamp,*[values[k] for k in FIELDS]))
        else:
            db().execute('''INSERT INTO client_contract_email_confirmations(client_id,acceptance_key,email,confirmed_at,accept_ip,accept_user_agent)
                VALUES(?,?,?,?,?,?)''',(owner['id'],acceptance_key(record),fresh['email'],stamp,request.remote_addr,request.headers.get('User-Agent','')[:500]))
        db().execute('DELETE FROM client_regularization_codes WHERE client_id=?',(owner['id'],));db().commit()
        flash('Pendências regularizadas com sucesso! Você já pode entrar com o Google usando seu cadastro existente.','success')
        return redirect(url_for('regularization.pending'))

    @bp.get('/contratos')
    @login_required
    def history():
        owner=client()
        rows=records(db(),owner)
        return render_template('client_contract_history.html',client=owner,records=rows,confirmations=db().execute('SELECT * FROM client_contract_email_confirmations WHERE client_id=? ORDER BY id DESC',(owner['id'],)).fetchall())

    @bp.get('/contratos/<int:record_id>/pdf')
    @login_required
    def record_pdf(record_id):
        from flask import send_file
        from contract_pdf import build_contract_pdf
        row=db().execute('SELECT * FROM client_contract_records WHERE id=? AND client_id=?',(record_id,session['client_id'])).fetchone()
        if not row:abort(404)
        return send_file(build_contract_pdf(dict(row)),mimetype='application/pdf',as_attachment=True,download_name=f'contrato-historico-{record_id}.pdf')

    app.register_blueprint(bp)
