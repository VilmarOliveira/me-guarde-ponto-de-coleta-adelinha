import sqlite3

from app import create_app
from contract import CONTRACT_VERSION
from test_app import register
from test_customization import csrf,legacy_login


def test_privacy_public_truthful_notice_links_and_no_contract_changes(tmp_path):
    database=str(tmp_path/'privacy.db')
    app=create_app({'TESTING':True,'SECRET_KEY':'privacy-tests','DATABASE':database})
    client=app.test_client();assert register(client).status_code==200
    with sqlite3.connect(database) as db:before=db.execute('SELECT * FROM clients').fetchall()
    page=client.get('/privacidade')
    assert page.status_code==200
    for text in ('Política de Privacidade','AMOR INFINITO MARKETING E SOLUCOES EMPRESARIAIS','Ponto de Coleta Adelinha',
                 'mailto:pontodecoletaadelinha@gmail.com','Última atualização','3 de novembro de 2026','sessionStorage','ViaCEP',
                 'Não há integração ativa com provedor externo','Uso Limitado','openid','email','Minuta para revisão','não oferece painel de preferências','não define um prazo único'):
        assert text in page.text
    assert 'name="robots" content="noindex"' in page.text
    assert 'no-store' in page.headers['Cache-Control']
    assert client.get('/condicoes').status_code==200 and CONTRACT_VERSION in client.get('/condicoes').text
    with sqlite3.connect(database) as db:assert db.execute('SELECT * FROM clients').fetchall()==before
    for endpoint in ('/','/cliente/login','/login','/condicoes','/privacidade','/blog','/duvidas-frequentes'):
        html=client.get(endpoint).text
        footer=html.split('<footer>',1)[1].split('</footer>',1)[0]
        assert 'href="/condicoes"' in footer and 'href="/privacidade"' in footer
    home=client.get('/cadastro').text
    assert 'href="/privacidade?next=/cadastro"' in home
    assert home.count('name="terms_accept"')==1 and 'name="privacy_accept"' not in home
    assert 'consulta não autoriza publicidade' in home
    assert 'target="_blank" rel="noopener noreferrer"' in home


def test_privacy_backlink_cannot_leave_the_site(tmp_path):
    app=create_app({'TESTING':True,'SECRET_KEY':'privacy-tests','DATABASE':str(tmp_path/'back.db')})
    client=app.test_client()
    for target in ('https://evil.invalid','//evil.invalid','/\\evil.invalid','/\nevil.invalid'):
        page=client.get('/privacidade',query_string={'next':target})
        assert page.status_code==200
        assert '<a class="button ghost" href="/">Voltar</a>' in page.text
    assert 'href="/#cadastro">Voltar' in client.get('/privacidade',query_string={'next':'/#cadastro'}).text


def test_footer_documents_for_authenticated_customer_and_staff(tmp_path):
    app=create_app({'TESTING':True,'SECRET_KEY':'privacy-tests','DATABASE':str(tmp_path/'auth.db')})
    client=app.test_client();register(client)
    client.post('/cliente/login',data={'cpf':'52998224725','password':'senha-segura','csrf_token':csrf(client)})
    assert 'Documentos do site' in client.get('/cliente').text
    client.post('/cliente/sair',data={'csrf_token':csrf(client)})
    legacy_login(client)
    assert 'Documentos do site' in client.get('/painel').text
    assert client.get('/privacidade').status_code==200 and client.get('/condicoes').status_code==200
