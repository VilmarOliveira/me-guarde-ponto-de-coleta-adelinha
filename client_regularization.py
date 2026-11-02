"""Client-owned regularization, verified email codes and append-only evidence."""
import hashlib
import json
import secrets
import sqlite3
import psycopg
from database import DatabaseIntegrityError
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import generate_password_hash
from client_compliance import (FIELDS, compliance, archive_legacy, acceptance_key, records,
                               signature_data, valid_email, revision_id)
from core import valid_cpf
from email_challenges import issue as issue_email, verify as verify_email


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
    def show(owner,status=200,subject=None):
        record,missing,needs_accept,contract,scope=state(owner)
        subject=subject or ('profile' if any(x['group']=='profile' for x in missing) else 'contract')
        shown=[x for x in missing if (x['group']=='profile')==(subject=='profile')]
        return render_template('client_pending.html',client=owner,missing=shown,subject=subject,profile_missing=any(x['group']=='profile' for x in missing),submitted_signature=signature_data(request.form.get('signature','')) if request.method=='POST' else None,confirmed=request.method=='POST' and request.form.get('terms_accept')=='yes',needs_accept=needs_accept,
            contract=contract,scope=scope,record=record,signature_preview=signature_data(record.get('signature'))),status

    @bp.route('/pendencias/dados',methods=['GET','POST'])
    @login_required
    def profile():
        return process('profile')

    @bp.route('/pendencias/contrato',methods=['GET','POST'])
    @login_required
    def contract_pending():
        return process('contract')

    @bp.route('/pendencias',methods=['GET','POST'])
    @login_required
    def pending():
        # Compatibility entry point; each rendered page handles only one subject.
        return process()

    def process(subject=None):
        try:return process_request(subject)
        except (DatabaseIntegrityError,sqlite3.DatabaseError,psycopg.Error):
            db().rollback()
            if request.method!='POST':raise
            flash('Não foi possível salvar os dados. Nenhum aceite foi concluído. Tente novamente.','error')
            return show(client(),503,subject)

    def process_request(subject=None):
        owner=client()
        if request.method=='GET':return show(owner,subject=subject)
        action='send_code' if request.form.get('email_intent')=='send_code' else request.form.get('action','')
        if (subject=='profile' and action!='profile') or (subject=='contract' and action=='profile'):abort(400)
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
            db().execute('INSERT INTO audit_logs(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',(f"cliente:{owner['id']}",'editar_proprio_cadastro','cliente',owner['id'],'Dados salvos pelo próprio cliente',now().isoformat()))
            db().execute('DELETE FROM client_regularization_codes WHERE client_id=?',(owner['id'],));db().commit()
            flash('Dados atualizados com sucesso.','success');return redirect(url_for('regularization.profile'))
        record,missing,needs_accept,contract,scope=state(owner)
        if any(x['group']=='profile' for x in missing):
            flash('Resolva primeiro a pendência de dados cadastrais.','error');return show(owner,400,'contract')
        if not missing:
            flash('Seu cadastro já está regularizado.','success');return redirect(url_for('regularization.pending'))
        if request.form.get('scope')!=scope:
            flash('Os dados ou o contrato mudaram. Recarregue e solicite um novo código.','error');return show(owner,409)
        if action=='send_code':
            status,message=issue_email(db(),app.secret_key,owner,scope,send_code)
            if request.headers.get('X-Requested-With')=='XMLHttpRequest':return {'sent':status==200,'message':message},status
            flash(message,'success' if status==200 else 'error')
            return show(owner,status)
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
        valid=verify_email(db(),app.secret_key,owner,scope,request.form.get('code',''))
        if not valid or new_scope!=scope or new_accept!=needs_accept or any(x['group']=='profile' for x in new_missing):
            db().commit();flash('Código inválido ou expirado, ou cadastro alterado. Confira e solicite outro código quando necessário.','error');return show(fresh,400)
        stamp=now().isoformat()
        archive_legacy(db(),fresh,stamp)
        if needs_accept:
            values={key:None for key in FIELDS}
            values.update(public_id=fresh['public_id'],contract_public_id=fresh['public_id'],name=fresh['name'],cpf=fresh['cpf'],
                terms_version=contract['terms_version'],terms_text=contract['terms_text'],terms_display_label=contract.get('terms_display_label'),contract_revision_id=revision_id(contract['terms_version'],contract['terms_text']),
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
        flash('Dados atualizados com sucesso.','success')
        return redirect(url_for('client_actions.hub'))

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
