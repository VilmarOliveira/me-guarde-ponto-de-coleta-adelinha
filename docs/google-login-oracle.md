# Contrato v3 e login com Google: implantação na Oracle

## O que é entregue

O contrato vigente é `CONTRATO-2026-v3`. A cláusula 5.11 contém o compromisso de análise e pagamento em até 30 dias corridos, sem condicioná-lo à conclusão de investigação ou recebimento de seguro. O próprio texto esclarece que é compromisso contratual do ponto, não prazo obrigatório em lei. A cláusula 5.9 mantém a responsabilidade por avaria e remete as demais ocorrências à 5.11, evitando repetir regras.

O site mostra a versão real; `/condicoes/pdf` disponibiliza o PDF vigente **sem aceite**. Os PDFs assinados e a visualização dos aceites antigos continuam lendo somente o texto e metadados históricos de cada cliente. A v2 original também foi arquivada em `contract_v2.py`. Nenhum aceite antigo é substituído ou migrado para v3. Cadastros novos enviam versão e hash do texto apresentado; o código de confirmação é vinculado a ambos. Uma tela antiga deve ser recarregada e um novo código solicitado antes do aceite.

O Google é uma alternativa ao login por CPF ou e-mail e senha. Não cria clientes. Exige cadastro ativo, dados obrigatórios, senha, assinatura, versão/texto do contrato, comprovante de aceite e confirmação por e-mail. Aceites completos de versões anteriores continuam válidos para este acesso; a atualização não exige reaceite retroativo. Cadastros legados sem essas evidências devem ser regularizados com a equipe; não se deve fabricar confirmação ou assinatura no banco.

O primeiro vínculo exige **a senha atual do cadastro**, além do e-mail confirmado pelo Google. Depois, a identidade é reconhecida pelo `sub` do Google, nunca remapeada por simples coincidência de e-mail. Duplicidade de e-mail impede vinculação até regularização; uma identidade por cliente e um cliente por identidade são garantidos por restrições no banco. Alteração de e-mail no Google não muda o proprietário do vínculo. O usuário mantém o acesso por senha e sua recuperação. Este fluxo cria apenas sessão de cliente e não concede permissões de equipe.

## Google Cloud

1. Entre no projeto Google Cloud que deseja usar. **Crie uma credencial OAuth nova e exclusiva para login**, mesmo que use o mesmo projeto da Gmail API. Não reutilize, substitua ou revogue o cliente, segredo ou refresh token da Gmail API.
2. Em **Google Auth Platform**, configure Branding/Tela de consentimento: nome Ponto de Coleta Adelinha, e-mail de suporte do estabelecimento, contato do desenvolvedor e o site institucional. Configure a audiência Externa para clientes externos. Se ficar em modo de teste, inclua os e-mails dos usuários de teste; publique para liberar ao público após a homologação e exigências apresentadas pelo Google.
3. Em acesso a dados/escopos, use somente `openid` e `email` (`https://www.googleapis.com/auth/userinfo.email` na interface do Google). O fluxo não pede permissão para Gmail, não envia e-mails pelo login e não pede acesso offline/refresh token. O nome/foto de perfil não é necessário para autorizar a conta.
4. Em **Clientes → Criar cliente OAuth → Aplicativo da Web**, use um nome como `Adelinha — Login de clientes`. Configure:

   | Campo | Valor exato |
   |---|---|
   | URI de redirecionamento autorizada | `https://pontodecoletaadl.servehttp.com/cliente/google/retorno` |
   | Origens JavaScript autorizadas | Não são necessárias: este fluxo usa redirecionamento e troca de código no servidor. Pode deixar vazio. Se preencher, use somente `https://pontodecoletaadl.servehttp.com`, sem caminho nem barra final. |

   Não cadastre `/cliente/login`, `/cliente/google/iniciar` ou o endereço da Gmail API como retorno deste cliente. Não acrescente barra final, parâmetros ou fragmento à URI de retorno. A URI deve coincidir integralmente com a variável abaixo.
5. Copie o **ID do cliente** e o **segredo do cliente** diretamente para o arquivo protegido da Oracle. Não os coloque em PRs, capturas, arquivos do repositório ou comandos de terminal que fiquem no histórico.
6. Se o Google exigir verificação de domínio, use um domínio sob controle do estabelecimento e cumpra essa verificação. A posse de um subdomínio de DNS dinâmico não implica controle de `servehttp.com`. Se for necessário trocar o domínio, atualize a URI de retorno no Google, a variável correspondente, HTTPS/proxy e os endereços de homologação. O código não depende de um hostname fixo.

## Variáveis em `/etc/ponto-coleta/site.env`

Edite com `sudoedit /etc/ponto-coleta/site.env`. **Acrescente ou ajuste estas linhas, sem substituir o restante do arquivo:**

```dotenv
GOOGLE_LOGIN_ENABLED=0
GOOGLE_LOGIN_CLIENT_ID=ID_DO_NOVO_CLIENTE_WEB
GOOGLE_LOGIN_CLIENT_SECRET=SEGREDO_DO_NOVO_CLIENTE_WEB
GOOGLE_LOGIN_REDIRECT_URI=https://pontodecoletaadl.servehttp.com/cliente/google/retorno
```

| Variável | Finalidade |
|---|---|
| `GOOGLE_LOGIN_ENABLED` | `0` desativa; `1` ativa somente quando as outras três configurações estão completas e o retorno é HTTPS válido. |
| `GOOGLE_LOGIN_CLIENT_ID` | Destinatário dos tokens e identificação da credencial Web exclusiva do login. |
| `GOOGLE_LOGIN_CLIENT_SECRET` | Segredo usado somente no servidor para trocar o código de autorização. |
| `GOOGLE_LOGIN_REDIRECT_URI` | URI fixa de retorno autorizada no Google; nunca derivada do cabeçalho Host enviado pelo navegador. |

Preserve `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`, `GMAIL_SENDER`, `DATABASE_URL`/`DATABASE`, `SECRET_KEY` e demais variáveis existentes. Nenhuma variável `GMAIL_*` é lida ou alterada por esta integração. Mantenha `SECRET_KEY` forte e persistente entre reinicializações e workers. Em produção HTTPS, mantenha `COOKIE_SECURE=1`; o cookie usa HttpOnly e SameSite=Lax, compatível com o retorno GET do Google.

Proteja o arquivo:

```bash
sudo chown root:root /etc/ponto-coleta/site.env
sudo chmod 600 /etc/ponto-coleta/site.env
```

## Atualização e serviço

A instalação Oracle não foi acessada nesta tarefa. O nome real do serviço e a pasta da instalação precisam ser conferidos no servidor; o repositório anterior usa `me-guarde.service` como exemplo. Descubra o serviço e seus caminhos sem exibir variáveis/segredos:

```bash
systemctl list-unit-files --type=service | grep -E 'ponto|guarde|gunicorn'
sudo systemctl show me-guarde.service -p WorkingDirectory -p EnvironmentFiles -p User
```

Nos comandos seguintes, substitua `me-guarde.service` pelo serviço encontrado e entre na pasta `WorkingDirectory` informada. Antes de atualizar, faça o backup/snapshot já utilizado para o banco; preserve alterações locais. **Após o merge da PR**, com checkout limpo na branch `main`:

```bash
git status --short
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
env -u DATABASE_URL -u DATABASE -u TEST_DATABASE_URL .venv/bin/python -m pytest -q
```

Os testes usam dados temporários; não forneça uma conexão de produção a `TEST_DATABASE_URL`. A inicialização cria as três tabelas novas com `CREATE TABLE IF NOT EXISTS`, sem modificar clientes, contratos, assinaturas, pagamentos ou comprovantes antigos. Instale primeiro com `GOOGLE_LOGIN_ENABLED=0`, preservando o acesso por senha.

Confirme que o serviço carrega `/etc/ponto-coleta/site.env`. Se `EnvironmentFiles` não incluir o arquivo, use `sudo systemctl edit me-guarde.service` e adicione, sem remover outras configurações:

```ini
[Service]
EnvironmentFile=/etc/ponto-coleta/site.env
```

### Evitar códigos OAuth nos logs

O retorno OAuth contém um código temporário e um `state` na query string. Não registre URLs completas desse retorno. Não habilite debug HTTP, nem imprima tokens, segredos, configurações ou respostas do provedor.

Se o Gunicorn tiver access log habilitado, ajuste o `access_log_format` em sua configuração (ou o argumento equivalente no ExecStart existente), preservando o comando e demais opções:

```python
access_log_format = '%(h)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s'
```

`%(U)s` é o caminho **sem query string**; não use `%(r)s` nem `%(q)s`. Não substitua o ExecStart sem conferir a configuração instalada.

Se houver Nginx, defina em seu bloco `http` um formato sem argumentos e use-o no bloco `server` deste site, substituindo o access log anterior desse mesmo bloco:

```nginx
log_format ponto_sem_query '$remote_addr [$time_local] "$request_method $uri $server_protocol" $status $body_bytes_sent';
# Dentro do server deste site:
access_log /var/log/nginx/ponto-coleta-access.log ponto_sem_query;
```

Não mantenha também outro `access_log` que registre `$request` ou `$request_uri` com os argumentos. Após ajustar:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

Se usa outro proxy, aplique a regra equivalente de não registrar query string no callback. Isso deve ser feito antes de habilitar o login real.

### Aplicar

Depois de configurar a credencial e os logs, altere `GOOGLE_LOGIN_ENABLED=1` no arquivo protegido:

```bash
sudoedit /etc/ponto-coleta/site.env
sudo systemctl daemon-reload
sudo systemctl restart me-guarde.service
sudo systemctl status me-guarde.service --no-pager
curl --fail --silent --show-error https://pontodecoletaadl.servehttp.com/health
```

`/health` deve retornar `{"status":"ok"}`. A alteração do arquivo de ambiente exige reiniciar os workers; não requer novo código. Não execute `source site.env` para exportar segredos no terminal e não use `systemctl show -p Environment` ou `printenv` para compartilhar diagnóstico.

## Homologação real após configurar a credencial

1. Abra `https://pontodecoletaadl.servehttp.com/condicoes` e o PDF `/condicoes/pdf`; confira v3 e a cláusula. Abra um aceite antigo e seu PDF, confirmando o texto e a versão históricos.
2. Abra `https://pontodecoletaadl.servehttp.com/cliente/login` em computador e celular. Confira CPF/e-mail e senha e o botão Google. Com a opção desativada ou incompleta, o botão deve sumir e o acesso por senha continuar funcionando.
3. Com cliente completo e ativo: Google → escolha a mesma conta de e-mail → confirme a senha do cadastro apenas no primeiro vínculo → Área do Cliente. Saia e repita: a mesma identidade entra sem pedir a senha novamente. Verifique que `/painel` e as ações da equipe continuam exigindo login de equipe.
4. Com e-mail sem cadastro: conferir a mensagem junto do formulário de cadastro, sem conta nova criada. Com cadastro incompleto ou inativo: conferir orientação/bloqueio, sem acesso liberado.
5. Cancele a escolha da conta; tente senha incorreta na vinculação; confira recuperação de senha. Teste indisponibilidade e token inválido exclusivamente em ambiente de homologação, nunca adulterando credenciais ou rede de produção.
6. Confira que o envio de códigos pela Gmail API continua funcionando com as variáveis anteriores. Este passo usa o remetente e a integração já autorizados no estabelecimento.

Desativação rápida: defina `GOOGLE_LOGIN_ENABLED=0` e reinicie o serviço. Os vínculos ficam preservados; CPF/e-mail e senha continuam disponíveis. As sessões de cliente já emitidas seguem as mesmas regras de expiração/revogação do sistema.

## Evidência e limites

Testes automatizados usam tokens RSA assinados com chave de teste, validando a assinatura pela biblioteca google-auth (somente a obtenção dos certificados e a troca de código são simuladas). Incluem claims, nonce, expiração, state, replay, CSRF, primeira vinculação por senha, limites de tentativas, ausência de cadastro, dados incompletos, inatividade, conflitos de e-mail/identidade, isolamento da equipe, preservação da v2 e novo aceite v3 confirmado por código.

A verificação visual em Chromium usa provedor Google simulado, SQLite temporário e nenhum envio real de mensagens. Não substitui a homologação da credencial real no domínio autorizado. Credenciais OAuth reais, Google Cloud e Oracle não estavam disponíveis nesta tarefa; a ativação em produção depende dos passos acima. Dispositivos físicos e Safari/iOS não foram testados.

Resultado final nesta revisão: **167 testes aprovados**, incluindo PostgreSQL 16 isolado, e **24 cenários de navegador** em 320, 390, 768 e 1440 px, além do fallback por senha e rascunhos de contrato. Dependências verificadas com `pip check`.
