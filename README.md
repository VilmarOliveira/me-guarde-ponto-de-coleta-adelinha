# Ponto de Coleta Adelinha

Sistema web responsivo do serviço **ME GUARDE — COMPROU? DEIXA COM A GENTE.**, operado pelo Ponto de Coleta Adelinha. Inclui cadastro público, gestão interna de pacotes, aviso manual por WhatsApp, cálculo de prazo e retirada com comprovante. Em produção, os dados ficam no PostgreSQL do Neon; SQLite é usado somente nos testes e na execução local.

## Link da prévia

**[Abrir a versão de teste do Ponto de Coleta Adelinha](https://ponto-coleta-adelinha-teste.onrender.com)**

O endereço acima corresponde ao serviço `ponto-coleta-adelinha-teste` configurado em `render.yaml`. A versão nova aparecerá nesse mesmo link depois que esta branch for enviada ao GitHub, mesclada na branch acompanhada pelo Render e o deploy terminar com sucesso. O link não muda a cada atualização.

Para confirmar que a atualização entrou no ar, abra também o [health check da prévia](https://ponto-coleta-adelinha-teste.onrender.com/health). Ele deve mostrar `{"status":"ok"}`. Se o Render estiver configurado para exigir autenticação de acesso à prévia, entre primeiro com a conta autorizada no painel do Render.

## 1. Executar localmente (passo a passo)

Requer Python 3.11 ou superior.

1. Instale o **Python 3.11 ou mais recente** pelo site oficial do Python. No Windows, marque “Add Python to PATH”.
2. Baixe este projeto e abra um Terminal dentro da pasta dele.
3. No Linux/macOS, execute `chmod +x scripts/setup.sh && ./scripts/setup.sh`. O script cria um ambiente isolado, instala tudo e roda os testes.
4. Inicie com `.venv/bin/python app.py`. No Windows, dê dois cliques em `iniciar.bat`; ele cria e utiliza o ambiente `.venv-local` automaticamente.
5. Abra `http://localhost:5000` no navegador. Para encerrar, volte ao Terminal e pressione `Ctrl+C`.

Para incluir o exemplo fictício antes de iniciar:

```bash
.venv/bin/python seed_demo.py
```

O painel fica em `http://localhost:5000/login`. Configure as credenciais administrativas exclusivamente por variáveis de ambiente privadas; elas não são publicadas neste repositório.

### Se aparecer `403 Forbidden` ao instalar

Esse erro não vem do sistema Adelinha: significa que a rede bloqueou o acesso do `pip` ao PyPI. Neste ambiente automatizado, tanto PyPI quanto os repositórios do Ubuntu foram bloqueados pelo proxy; trocar a versão do Flask não resolve. Tente, nesta ordem:

1. Execute o processo em sua internet residencial ou compartilhe a internet do celular.
2. Se estiver numa empresa/escola, peça ao suporte o endereço do **espelho PyPI** e execute `PIP_INDEX_URL=https://ENDERECO-DO-ESPELHO/simple ./scripts/setup.sh`.
3. Como alternativa, instale o Docker Desktop e execute `docker build -t adelinha-teste .` e depois `docker run --rm -p 5000:5000 -v adelinha-dados:/data adelinha-teste`. O Docker também precisa de acesso à internet na primeira construção.

Não desative o antivírus, o certificado TLS ou a verificação HTTPS para contornar o erro.

## Testar

```bash
pytest -q
```

Os testes cobrem proteção do painel, limites exatos das categorias, bloqueio de excesso, cobrança por datas corridas e retirada parcial/duplicada.

### Personalização e equipe

Na **Área do Ponto**, as opções ficam agrupadas em **Configurações**. Administradores podem gerenciar identidade visual, banners, fotos reais do local, aparência, Pix e integrações. Os banners (até três imagens ativas PNG, JPG ou WebP de no máximo 900 KB, com medidas recomendadas de 2048 × 684 px), fotos e temas ficam no mesmo banco PostgreSQL/Neon, por isso sobrevivem a novos deploys sem disco do Render.

Crie uma conta de **Administrador**, saia e confirme um login nela antes de excluir o acesso administrativo anterior. O sistema bloqueia a exclusão, desativação ou mudança de perfil que deixaria o sistema sem administrador ativo. Os perfis Gerente e Atendente recebem apenas permissões operacionais; contas desativadas ou excluídas têm suas sessões invalidadas. Não coloque credenciais pessoais no código ou em variáveis versionadas.

A confirmação do contrato e a recuperação de senha de clientes e da equipe ocorrem **somente pelo e-mail cadastrado**, usando a configuração Gmail abaixo. WhatsApp continua disponível somente para suporte e avisos manuais de chegada; o servidor rejeita solicitações diretas de códigos por WhatsApp.

O endpoint `http://localhost:5000/health` deve responder `{"status":"ok"}` quando o servidor e o banco estiverem funcionando.

## Configuração para produção

Defina variáveis de ambiente antes de iniciar:

```bash
export SECRET_KEY="gere-uma-chave-longa-e-aleatoria"
export STAFF_USER="usuario-da-equipe"
export STAFF_PASSWORD_HASH="$(python -c 'from werkzeug.security import generate_password_hash; print(generate_password_hash("SENHA-FORTE-AQUI"))')"
export DATABASE_URL="postgresql://USUARIO:SENHA@HOST/BASE?sslmode=require"
export PORT=5000
export COOKIE_SECURE=1
```

Copie a conexão no painel do Neon e cadastre-a como segredo `DATABASE_URL`; nunca coloque a URL ou a senha no código, GitHub ou `render.yaml`. Execute atrás de HTTPS com um servidor WSGI, por exemplo `gunicorn 'app:create_app()'`. A inicialização cria somente tabelas e colunas ausentes e não apaga registros. Configure backups no Neon.

### Envio de e-mail pela Gmail API (HTTPS)

Os códigos de recuperação, confirmação de contrato e lembretes por e-mail são enviados pela Gmail API usando exclusivamente o remetente `pontodecoletaadelinha@gmail.com`. O cabeçalho usa o nome **Ponto de Coleta Adelinha**, as respostas são direcionadas à mesma conta e todas as mensagens recebem a assinatura **ME GUARDE — COMPROU? DEIXA COM A GENTE.** O servidor renova o token de acesso automaticamente com o Refresh Token e só informa sucesso quando a Gmail API devolve um ID de mensagem. Nenhum teste automatizado envia mensagens reais.

#### 1. Gerar a autorização no Windows

Não envie o JSON OAuth ao GitHub. No **CMD**, dentro da pasta do projeto, execute exatamente:

```bat
.venv-local\Scripts\python.exe scripts\authorize_gmail.py "C:\caminho\para\client_secret.json"
```

O script abre o navegador, solicita somente `gmail.send`, pede autorização para `pontodecoletaadelinha@gmail.com` e cria `gmail-render.env` na pasta atual. Ele **não envia e-mail**. O arquivo contém segredos, já está ignorado pelo Git e não deve ser compartilhado. Se o ambiente virtual ainda não existir, execute `iniciar.bat` uma vez e feche o servidor antes do comando acima.

Se o aplicativo OAuth estiver em modo de teste, mantenha `pontodecoletaadelinha@gmail.com` como usuário de teste. Como o projeto é **Externo** e está em **Testing**, o Google limita a sete dias o Refresh Token emitido para `gmail.send`; nesse estado será necessário autorizar novamente e substituir `GMAIL_REFRESH_TOKEN` quando ele expirar. Para uso contínuo, revise os requisitos do Google e altere o status de publicação para **In production**. Isso evita a expiração específica de sete dias, mas não torna o token permanente: ele ainda pode ser revogado, ficar sem uso por longo período ou ser invalidado por mudanças na conta. O sistema trata essa situação como falha, sem expor o token.

Se o Google não devolver um Refresh Token, revogue o acesso anterior da aplicação na Conta Google e execute o comando novamente.

**Substituir a autorização antiga de `vilmar920@gmail.com`:** execute o script com o JSON do projeto “Me Guarde - PCA”, escolha `pontodecoletaadelinha@gmail.com` no navegador e substitua, na mesma atualização do Render, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` e `GMAIL_SENDER`. Não reutilize Client ID, Client Secret ou Refresh Token da conta antiga. Depois de confirmar o novo envio, revogue o acesso antigo na Conta Google e remova qualquer cópia local antiga do arquivo de autorização.

#### 2. Configurar no Render

1. Abra o serviço no Render e entre em **Environment**.
2. Abra localmente `gmail-render.env` e copie cada valor para uma variável secreta de mesmo nome:

```text
GMAIL_CLIENT_ID=valor gerado pelo script
GMAIL_CLIENT_SECRET=valor gerado pelo script
GMAIL_REFRESH_TOKEN=valor gerado pelo script
GMAIL_SENDER=pontodecoletaadelinha@gmail.com
```

3. Não use aspas e não inclua espaços antes ou depois dos valores.
4. Salve as variáveis e aguarde o novo deploy/reinício.
5. Na Área do Ponto, abra **Envio de códigos** e confirme que E-mail aparece como **Configurado**.
6. Faça uma única recuperação para um cadastro de teste autorizado e confira o recebimento. A tela registra apenas o retorno seguro da API, nunca tokens ou o código enviado.

Não são mais necessárias variáveis `SMTP_*`. Um retorno “aceito para processamento” significa que a Gmail API aceitou a mensagem; não é uma confirmação de leitura. Se a renovação for revogada, a API estiver indisponível ou rejeitar a mensagem, o sistema apresenta falha e permite tentar novamente, sem informar falsamente que houve envio.

#### WhatsApp Cloud API

```text
WHATSAPP_ACCESS_TOKEN=token-permanente
WHATSAPP_PHONE_NUMBER_ID=id-do-numero
WHATSAPP_TEMPLATE_NAME=nome_do_modelo_aprovado
WHATSAPP_TEMPLATE_LANGUAGE=pt_BR
WHATSAPP_GRAPH_VERSION=versao-ativa-da-API
```

Use em `WHATSAPP_GRAPH_VERSION` uma versão ainda ativa informada no painel/documentação da Meta, incluindo o prefixo `v`. O modelo aprovado precisa ter um parâmetro no corpo destinado ao código.

### Migrar o SQLite existente para o Neon

1. Pare o sistema e faça uma cópia de `data\adelinha.db`.
2. Crie um projeto/banco vazio no Neon.
3. Defina `DATABASE_URL` apenas no terminal ou como segredo do serviço.
4. Execute `python scripts/migrate_sqlite_to_postgres.py data/adelinha.db`.
5. Confira as quantidades e alguns cadastros no Neon antes de apontar o Render para a nova conexão.

O script copia somente colunas existentes, respeita a ordem das chaves estrangeiras, não remove dados e não sobrescreve chaves que já existam no PostgreSQL. Guarde o SQLite original até validar completamente a migração.

## 2. Salvar suas alterações no GitHub

Crie uma conta gratuita no GitHub e um repositório **privado**, sem adicionar README ou `.gitignore` pela tela. Dentro desta pasta, execute (troque o endereço pelo exibido no seu repositório):

```bash
git status
git add .
git commit -m "Descreva aqui a alteração"
git remote add origin https://github.com/SEU-USUARIO/SEU-REPOSITORIO.git
git push -u origin work
```

Nas próximas alterações, bastam `git add .`, `git commit -m "mensagem"` e `git push`. Se o GitHub pedir senha no Terminal, use um **Personal Access Token** ou entre com GitHub Desktop; a senha comum da conta não é aceita para `git push`.

Não envie o arquivo `data/adelinha.db`: ele contém dados pessoais e já está ignorado pelo Git. Nunca coloque senhas, `SECRET_KEY` ou cópias do banco no GitHub.

## 3. Abrir uma versão de teste na internet

O projeto inclui `render.yaml`, pronto para uma prévia no plano Free do Render, sem disco. A persistência é fornecida pelo PostgreSQL do Neon. Antes de começar, gere o hash da sua senha:

```bash
.venv/bin/python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('COLOQUE-UMA-SENHA-FORTE-AQUI'))"
```

Copie toda a linha exibida. Depois:

1. Acesse Render, entre com GitHub e escolha **New > Blueprint**.
2. Autorize apenas o repositório privado deste projeto e selecione-o.
3. Confirme o arquivo `render.yaml` e o serviço sugerido.
4. Quando solicitado, cole a linha no valor de `STAFF_PASSWORD_HASH`; marque-a como secreta. Não use a senha de demonstração. Cole também a conexão do Neon em `DATABASE_URL`, como segredo.
5. Aguarde o deploy e abra a URL terminada em `.onrender.com`. Teste `/health`, faça um cadastro fictício, registre um pacote, confirme o aviso e faça uma retirada.
6. Verifique `/health` e confirme no Neon que as tabelas foram criadas. O serviço não usa nem precisa de disco persistente no Render.

Essa URL é uma **versão de teste**. Não cadastre CPFs ou documentos reais até concluir revisão jurídica/LGPD, backup, controle de acesso e testes operacionais. Quando estiver satisfeito, conecte um domínio em **Settings > Custom Domains** e mantenha HTTPS habilitado.

## 4. Serviços, contas e custos

1. **Domínio** (opcional, recomendado): conta em um registrador, normalmente R$ 40–70/ano para `.com.br`.
2. **Hospedagem HTTPS**: o `render.yaml` usa o plano Free do Render. Confira limites e preços atuais antes de publicar.
3. **Banco PostgreSQL**: conta e projeto no Neon, com a conexão guardada somente em `DATABASE_URL`. Confira limites e preços atuais no Neon.
4. **Repositório Git privado**: GitHub/GitLab possuem opções gratuitas.
5. **Backup externo criptografado**: armazenamento em nuvem; o custo depende de retenção e volume.
6. **WhatsApp**: nesta versão o envio é manual pelo link `wa.me`, sem API paga.
7. **Pix**: conta bancária empresarial apta a receber pela chave CNPJ. Eventuais tarifas dependem do banco.

Antes da produção: revise o contrato com profissional jurídico/LGPD, troque as credenciais, restrinja quem acessa o painel, configure HTTPS, backups e política de retenção, e faça teste de restauração. CPF, documentos e assinaturas nunca são mostrados na área pública; por conter dados pessoais, o banco e seus backups devem ter acesso rigorosamente limitado.

## Atualizar no Windows sem perder dados

1. **Pare o sistema** e localize `data\adelinha.db` (ou o caminho definido em `DATABASE`).
2. Copie esse arquivo para uma pasta segura, com data no nome, por exemplo `Backup\adelinha-2026-09-30.db`. Não continue sem esse backup.
3. Abra PowerShell na pasta do projeto e execute `git status`. Se houver alterações suas, salve-as com `git add .` e `git commit -m "backup antes da atualização"`.
4. Execute `git pull origin work` para baixar a nova versão.
5. Execute `iniciar.bat` com dois cliques. Ele cria `.venv-local`, instala `requirements.txt` (incluindo `tzdata`) e abre o sistema em `http://localhost:5000`.
6. Para executar manualmente pelo CMD, use exatamente:
   ```bat
   cd C:\caminho\da\pasta\do\projeto
   py -3 -m venv .venv-local
   .venv-local\Scripts\python.exe -m pip install -r requirements.txt
   .venv-local\Scripts\python.exe -m pytest -q
   .venv-local\Scripts\python.exe app.py
   ```
7. Na primeira inicialização, a migração adiciona as novas colunas e tabelas sem recriar clientes, IDs, assinaturas, pacotes ou retiradas. Clientes antigos continuam sem senha até a equipe conferir a identidade e gerar o link temporário no cadastro completo.
8. Confira alguns registros. Se houver problema, pare o sistema e restaure a cópia do banco feita no passo 2.

Nunca substitua nem apague `adelinha.db` durante uma atualização. Faça também uma cópia antes de cada deploy ou mudança de versão.

Nesta atualização, a inicialização também acrescenta os campos de histórico do comprovante. Novas retiradas passam a guardar uma fotografia dos IDs, nome do cliente, valores, atraso e pagamento. Retiradas antigas são preservadas; campos que nunca foram gravados aparecem como **“Não registrado”**. A geração do PDF usa `reportlab`, instalado automaticamente pelo `requirements.txt`.

### Banners do topo

Em **Configurações → Banners**, o administrador gerencia a imagem Me Guarde e os outros dois banners em uma única tela: prévia, substituição, ordem e ativação. O carrossel usa a área abaixo do menu, exibe a imagem completa e alterna a cada três segundos, com indicadores e navegação por toque. A rotação pausa enquanto o mouse está sobre o carrossel ou o foco do teclado está dentro dele, e fica desativada quando o dispositivo solicita movimento reduzido. Uma única imagem ativa aparece sem controles. O limite é de **900 KB (921.600 bytes)** por arquivo PNG, JPG ou WebP; recomendação: **2048 × 684 px**.

A migração preserva as imagens anteriores e inclui o banner principal na rotação. Se já existiam três banners adicionais, o terceiro é preservado e desativado: ele permanece disponível na administração, onde é possível desativar outra imagem para ativá-lo. A ordem, as imagens e a ativação ficam no banco e sobrevivem aos reinícios. Uploads inválidos mantêm a configuração e a imagem anterior.

### Dados do Pix

Os dados iniciais são o titular **AMOR INFINITO MARKETING E SOLUCOES EMPRESARIAIS** e a chave CNPJ `38145273000105`. Em **Configurações → Pix → Editar dados do Pix**, somente o administrador pode alterar titular, tipo e chave. A tela mostra o nome completo. São aceitas chaves CPF/CNPJ com dígitos verificadores válidos, e-mail de até 77 caracteres, telefone internacional com `+` e chave aleatória UUID. Cancelar não salva alterações. Os novos dados passam a ser usados nas próximas cobranças; cada retirada Pix guarda seus próprios dados para preservar os comprovantes. Ao gerar o QR Code e o Pix Copia e Cola, o sistema aplica automaticamente o limite de 25 caracteres do padrão técnico Pix; a conferência do crédito continua manual.

### Migração dos endereços

A primeira inicialização acrescenta campos separados para o endereço residencial e o e-mail sem apagar registros. Cadastros antigos ficam identificados como **Não cadastrado** até o cliente ou o atendente preencher os dados. Novos cadastros exigem CEP, rua, número, bairro, cidade e estado. A busca via ViaCEP auxilia o preenchimento, mas os campos continuam disponíveis para correção manual. O endereço Pickup permanece separado, não é copiado para o residencial e não pode ser alterado pelo cliente.

### O que a migração preserva

A inicialização acrescenta, sem apagar tabelas, os campos de cliente ativo e versão de sessão, tipo de documento, confirmação de pagamento, auditoria e configuração Pix. Contratos, IDs, assinaturas, pacotes, retiradas e comprovantes existentes permanecem no mesmo banco. Informações que não eram gravadas em registros antigos aparecem como **Não registrado**.

## Regras implementadas

- IDs de cliente `CLIPCA-1` e pacote `PCTPCA-1`. Na primeira inicialização, códigos `CLTPCA` são atualizados para `CLIPCA`, mantendo o número quando disponível e a busca pelo código antigo. A sequência continua sem reutilizar números já emitidos; contratos e comprovantes históricos mantêm o código original.
- CPF aceito com ou sem pontuação, validado pelos dígitos verificadores e bloqueado quando já pertence a outro cadastro.
- Aceite guarda versão, assinatura e data/hora em `America/Sao_Paulo`.
- Pequeno: soma ≤ 80 cm **e** peso ≤ 10 kg; grande: soma ≤ 150 cm **e** peso ≤ 20 kg; acima disso é recusado.
- Confirmação explícita inicia o prazo; abrir/reabrir WhatsApp não o altera.
- O dia do aviso é o primeiro dos quatro; R$ 0,50 por data corrida desde o quinto dia.
- Retirada total ou parcial por Pix ou dinheiro, sempre com confirmação manual, identificação e assinatura; pacote retirado não pode ser retirado novamente.
- Desconto opcional na retirada, em reais ou porcentagem, calculado sobre o total com atraso e preservado no histórico, recibo e PDF.
- Acréscimo manual opcional, separado da taxa automática de atraso, com motivo obrigatório e histórico do atendente responsável.
- Comprovante persistente e imprimível/salvável em PDF pelo navegador.

### Descontos na retirada

Somente um atendente autenticado pode aplicar desconto. Na Área do Ponto, informe **Desconto em valor (R$)** ou **Desconto em porcentagem (%)**; preencher uma modalidade limpa a outra. O servidor recalcula e valida o total, e o valor final é usado tanto no Pix quanto no recebimento em dinheiro. A migração acrescenta os campos históricos de desconto sem modificar retiradas antigas, que continuam exibindo os valores originalmente gravados.

### Acréscimo na retirada

O atendente pode informar um acréscimo monetário não negativo com até duas casas decimais. Quando o valor for maior que zero, o motivo é obrigatório. O sistema soma pacotes e atraso, aplica o desconto escolhido e, em seguida, adiciona o acréscimo manual. Valor, motivo, atendente, subtotal, desconto e total final ficam preservados no recibo, nos detalhes e no PDF. A migração adiciona esses campos sem alterar retiradas antigas.

## Testes no GitHub Actions

O workflow `.github/workflows/tests.yml` executa a suíte em cada Pull Request. Ele usa SQLite temporário para os testes funcionais e inicia um PostgreSQL 16 isolado para o teste de compatibilidade. Não utiliza `DATABASE_URL`, dados ou credenciais do Neon de produção e não exige segredo adicional no GitHub.

O Render continua usando somente a variável `DATABASE_URL` configurada no painel do serviço. Não altere essa variável e nunca copie sua conexão para arquivos do projeto, logs ou Pull Requests.

## Integrações externas da versão consolidada

A confirmação real de códigos exige `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` e `GMAIL_SENDER` para envio exclusivo por e-mail pela Gmail API. Sem elas o sistema informa indisponibilidade e nunca simula validação. Configure também `STAFF_NAME`, `PICKUP_CEP` e `PRIVACY_CONTACT` antes de liberar o novo contrato; os valores padrão correspondem aos dados já informados do estabelecimento.

O lembrete do terceiro dia deve executar `python scripts/send_reminders.py` uma vez por dia em um Render Cron Job ou serviço equivalente independente do processo web. O job é idempotente por pacote, revalida o status antes do envio e registra aceite ou falha do provedor. Render Cron e provedores de e-mail/WhatsApp podem ter custos conforme o plano; nenhum serviço é contratado automaticamente.

A identidade do cabeçalho é persistida no PostgreSQL. Imagens e páginas públicas possuem cache versionado; páginas autenticadas, contratos, comprovantes, códigos, pagamentos e QR Codes não entram no service worker.

Para SEO, cadastre o domínio real como domínio canônico na implantação, verifique-o no Google Search Console e envie `/sitemap.xml`. Indexação e posição nos resultados não são garantidas.


## Atualização da instalação na Oracle

1. Faça um backup/snapshot do Neon e confirme que `DATABASE_URL` continua configurada como segredo no serviço da Oracle.
2. Na pasta do aplicativo, execute `git pull --ff-only`, ative o ambiente virtual e rode `python -m pip install -r requirements.txt`.
3. Execute `python -m pytest -q` em um banco separado antes de reiniciar a produção. Não aponte `TEST_DATABASE_URL` para o Neon de produção.
4. Reinicie o processo Gunicorn pelo gerenciador usado na máquina (por exemplo, `sudo systemctl restart me-guarde`) e confira `/health`.

A inicialização executa apenas criações e alterações não destrutivas: preserva clientes, pacotes, contratos, pagamentos e comprovantes. Os códigos públicos da equipe e as métricas ficam no Neon; não é necessário disco persistente na Oracle. A medição registra somente identificador aleatório, página, origem técnica quando disponível, tipo de dispositivo e horário. Ela começa no primeiro acesso após esta versão, é estimativa e não coleta formulários, documentos, assinaturas, senhas ou códigos.


### Banner original, WhatsApp e abas de usuários

Consulte [o procedimento desta atualização na Oracle](docs/atualizacao-oracle.md) para instalar os arquivos originais, conferir o banner principal e validar as abas Clientes e Membros da equipe.

## Identidade institucional

O tema padrão usa laranja, azul-turquesa (`#00CED1`), grafite e branco, com Montserrat local e cores operacionais preservadas. Temas personalizados e imagens enviadas pelo administrador continuam disponíveis. “Restaurar cores padrão” aplica a nova identidade sem excluir os temas salvos.

Consulte [a documentação da identidade](docs/identidade-visual.md) para os recursos versionados, a preservação de documentos históricos e a pendência do arquivo original da logo.

## Envio de imagens

Todos os campos de imagem aceitam PNG, JPG ou WebP de até **900 KB por arquivo**, com validação no navegador e no servidor. Um arquivo inválido não substitui a imagem anterior. As medidas são recomendações; imagens existentes são preservadas e não são recortadas automaticamente.

| Uso | Medidas recomendadas |
| --- | --- |
| Banners | 2048 × 684 px, horizontal |
| Logo do cabeçalho | 640 × 640 px, quadrado |
| Fotos do local | 1200 × 900 px, horizontal (4:3) |
| Capas do blog | 1200 × 675 px, horizontal (16:9) |

## Responsividade

O mesmo sistema atende computador, tablet e celular. As regras de tela estão centralizadas em `static/responsive.css`; o menu se adapta ao espaço disponível, tabelas rolam apenas dentro de seus contêineres e assinaturas não são apagadas durante a rotação da tela.

Consulte [a revisão e os resultados](docs/responsividade.md) e [as instruções dos testes de navegador](tests/browser/README.md).


## Contrato v3 e acesso com Google

Nova cláusula de indenização com compromisso contratual de até 30 dias corridos, preservando aceites e PDFs históricos. O login com Google é exclusivo de clientes previamente cadastrados e exige senha no primeiro vínculo. Fica desativado por padrão, separado da Gmail API. Veja as [instruções completas de Google Cloud e Oracle](docs/google-login-oracle.md), incluindo retorno OAuth, variáveis de `/etc/ponto-coleta/site.env`, comandos de atualização, proteção de logs e homologação.

## Regularização do cadastro e série atual do contrato

A Área do Cliente mostra pendências reais e permite completar dados e confirmar/assinar contratos sem recriar o cadastro nem sobrescrever aceites antigos. O Google usa o mesmo diagnóstico, com compatibilidade para assinaturas históricas válidas. A série atual exibe **Contrato — Versão 1**, com código exclusivo `ADELINHA-SERIE-2026-11-v1`, sem renumerar v2/v3. Veja [diagnóstico, histórico, auditoria e implantação](docs/regularizacao-cadastro-e-contrato.md).

## Política de Privacidade e documentos públicos

A rota pública `/privacidade` e os links do rodapé organizam o acesso à política e aos Termos de Serviço existentes em `/condicoes`. A política está identificada como **minuta para revisão antes da publicação definitiva**, com pendências operacionais e fontes oficiais cuja consulta foi bloqueada no ambiente. Consulte [revisão, evidências e URLs para o Google Cloud](docs/privacidade-revisao.md). Documentos abrem em nova aba para preservar os dados do cadastro e a assinatura; não acrescentam consentimento para publicidade.
