# Plano ME GUARDE Mais — FastFlow e Oracle

Implementação de 3 de novembro de 2026. **Atualização da oferta: somente Pix para contratar/renovar. O checkout Card In está desabilitado no código, inclusive se variáveis antigas estiverem em `1`. Estrutura, dados e consultas de pagamentos antigos por cartão permanecem preservados. As instruções técnicas de cartão abaixo ficam como referência histórica e não habilitam o recurso nesta revisão.** A `main` não tinha uma base de plano; a criação da base foi autorizada pelo responsável. Nenhum banco de produção, credencial, e-mail real ou cobrança real foi utilizado nesta entrega.

Atualização administrativa: também é possível validar a participação manualmente, com origem e histórico próprios, sem declarar pagamento na FastFlow. Consulte [validação manual e cancelamento de solicitações não pagas](plano-validacao-administrador.md). As exigências de confirmação pela API abaixo continuam aplicáveis ao fluxo automático.

## Comportamento implementado

- R$ 33,90 por ciclo mensal, 10 créditos. Pequeno consome 1 e grande 2, com mistura de tamanhos. Valor fixado no servidor. Crédito não acumula.
- Contratação e renovação exigem sessão de cliente ativa, cadastro/assinatura/confirmação existentes válidos, ausência de pendência que restrinja a função `Contratar ou renovar o plano` e concordância explícita com as regras mostradas. Login por senha ou Google usa a mesma sessão e as mesmas verificações. Não há cadastro automático ou permissão de equipe nesse fluxo.
- O cliente inicia **cada** pagamento. Não há débito automático, armazenamento de cartão para uso futuro ou criação de cobranças pelos agendamentos.
- Pedido, texto/revisão das regras, identificação do cliente, transação, confirmações, ciclos, concessões e consumos são persistidos no banco configurado em `DATABASE_URL`/`DATABASE`. A atualização acrescenta tabelas, sem regravar contratos, assinaturas ou parcelas anteriores. Mantenha banco, backups e arquivo de ambiente persistentes na Oracle.
- `approved` é aprovação de compliance, não pagamento. Apenas `paid`, recebido da API e validado no servidor, libera o ciclo. O resumo do POST (inclusive Card In) é seguido de GET de detalhes antes da concessão.
- Confere ID, provedor `fastflow`, R$ 33,90, forma e `notification_url` exclusiva do pedido. Confere também o documento do pagador quando o provedor o retorna. A callback única vincula o pedido ao cliente autenticado; não é um segredo de autenticação. Não libera por parâmetros do navegador, comprovantes ou eventos sem consulta à API.
- Para cartão, `paid` e `receivable_status=scheduled` significam pagamento aprovado com recebível agendado, **não liquidação em conta**. O painel exibe as situações separadamente. Não são utilizados endpoints de antecipação, saque, alteração manual de status ou saldo.
- Pix usa o texto e validade da API e gera localmente o QR Code. A página consulta o servidor a cada 10 segundos, no máximo 60 vezes por abertura, pausando oculta. A consulta remota por pedido tem janela mínima de 20 segundos, com recuo após falha. Há botão de consulta manual. Um orçamento persistente compartilhado entre workers limita GETs a 15/minuto por grupo de endpoint (abaixo de 1000/h) e POSTs a 4/minuto; indisponibilidade ou limite mantém o pedido pendente para reconciliação. Não se declara expiração apenas pelo relógio local: a API determina o estado.
- Não existe chave de idempotência documentada para criar Pix. O banco reserva apenas uma cobrança em aberto por cliente/ciclo, serializa cliques e limita reservas globalmente a quatro/minuto. Após timeout/erro incerto, **nunca repete o POST**: consulta o ID existente ou procura a callback exclusiva na listagem documentada (até 20 páginas de 100 itens). Sem correspondência confiável, mantém o pedido em consulta. Um `creating` abandonado por queda do processo é recuperado da mesma forma. Isso privilegia evitar duplicidade; não se promete idempotência externa que a API não oferece.
- Expirada/cancelada/recusada com estado confirmado permite nova tentativa por ação do cliente. Estorno é terminal para o crédito daquela cobrança; evento antigo `paid` não o reativa. Pagamento novamente em análise ou com dados divergentes suspende novos usos e pede tratamento administrativo. Não apaga evidências nem recalcula cobrança de pacote coberto. Casos com créditos usados exigem atendimento, sem bloquear retirada anterior.
- Ciclo mensal em `America/Sao_Paulo`, com data e hora exibidas. Preserva dia de referência: 31/jan → último dia de fevereiro → 31/mar. Antecipação começa ao fim do ciclo vigente e só concede créditos quando começa; depois do vencimento, inicia na confirmação, sem cobertura retroativa.
- O registro de recebimento em `/pacotes` consome créditos **na mesma transação de banco** que cria o pacote. Sem saldo suficiente/plano vigente, aplica avulso integral ao novo pacote. Salva o valor original e o vínculo do consumo no histórico e zera o valor base do pacote coberto. Retirada não consome novamente; atraso continua separado. Pacotes cobertos não podem ser excluídos ou trocar de cliente/categoria silenciosamente, preservando o histórico. Outros dados operacionais podem ser corrigidos antes do aviso.
- Lembretes Gmail no penúltimo e último dia **da data de vencimento** (ex.: 29 e 30), com nome, valor, vencimento e botão de renovação. Renovação paga suprime os seguintes. Cada ciclo/dia tem reserva persistente; resultado incerto não é reenviado automaticamente para evitar duplicidade. `claimed`/`delivery_uncertain` exigem conferência no painel, sem reenvio cego. Não há envio publicitário.

## Documentação consultada e limitações do provedor

Base fixa `https://fastdepix.space/api/v1/`. A consulta online a `https://fastdepix.space/api/docs.php` foi bloqueada pelo proxy do ambiente; a implementação segue a documentação completa fornecida pelo responsável em anexo. Ela documenta `/auth/me`, `/transactions`, `/transactions/{id}`, `/card-in/fees`, `/card-in/charges` e HMAC SHA-256 dos webhooks. Não documenta sandbox separado, checkout hospedado ou tokenização para Card In. Os testes usam mocks exclusivamente em arquivos de teste/ambiente temporário, sem chave funcional ou caminho de simulação em produção. Conferir mudanças futuras com o suporte antes de habilitar a conta real.

## Variáveis exatas em `/etc/ponto-coleta/site.env`

Use `sudoedit /etc/ponto-coleta/site.env`. Acrescente as linhas, **sem substituir o restante**, em especial `DATABASE_URL`, `SECRET_KEY`, `GOOGLE_LOGIN_*` e `GMAIL_*` existentes:

```dotenv
FASTFLOW_ENABLED=0
FASTFLOW_API_KEY=SUBSTITUIR_LOCALMENTE_PELA_CHAVE_TIPO_FASTFLOW
FASTFLOW_WEBHOOK_SECRET=SUBSTITUIR_LOCALMENTE_PELO_SEGREDO_DO_PRIMEIRO_WEBHOOK_ATIVO
FASTFLOW_PUBLIC_ORIGIN=https://pontodecoletaadl.servehttp.com
FASTFLOW_CARD_ENABLED=0
FASTFLOW_CARD_PCI_READY=0
ME_GUARDE_MAIS_LOGO_PATH=/var/lib/ponto-coleta/me-guarde-plus.webp
```

- `FASTFLOW_ENABLED`: `1` habilita a integração após configuração; `0` mantém novos pagamentos indisponíveis. Histórico e cobertura de pacotes são mantidos mesmo com novos pagamentos desabilitados.
- `FASTFLOW_API_KEY`: chave Bearer **do tipo fastflow**, confirmada por GET `/auth/me` com `api_provider=fastflow`. Nunca enviar pelo chat ou colocar em JavaScript, URL, código, Git ou logs.
- `FASTFLOW_WEBHOOK_SECRET`: segredo fornecido pelo provedor, mínimo 32 caracteres. **Não gerar outro segredo local desconectado do painel**. A documentação diz que callbacks por cobrança usam o segredo do **primeiro webhook ativo do parceiro**. Garanta qual é esse webhook; se já há outros ativos, use o segredo correto e preserve integrações existentes.
- `FASTFLOW_PUBLIC_ORIGIN`: origem HTTPS pública, sem caminho, query ou fragmento. O valor acima utiliza o domínio já documentado do sistema; confirme DNS/HTTPS da instalação. Não depende do cabeçalho Host enviado pelo visitante.
- `FASTFLOW_CARD_ENABLED`: só `1` depois da liberação de Card In para a conta.
- `FASTFLOW_CARD_PCI_READY`: só `1` depois de concluir a avaliação/configuração de segurança da captura direta abaixo. Não é certificado nem substitui validação PCI.
- `ME_GUARDE_MAIS_LOGO_PATH`: caminho persistente de arquivo **aprovado** PNG/JPG/WebP, máximo 900 KB. Esse caminho opcional mantém os usos anteriores da Área do Cliente. A logo original recebida posteriormente acompanha o código em `static/brand/me-guarde-mais/logo-original.png` e é usada somente no cartão PLANO MENSAL da página inicial; não depende desta variável nem altera os demais usos. O usuário do serviço precisa poder ler o arquivo. Não inserir segredo nesse caminho.

```bash
sudo chown root:root /etc/ponto-coleta/site.env
sudo chmod 600 /etc/ponto-coleta/site.env
```

Não use `cat site.env`, `printenv`, `set -x`, comandos com a chave literal ou `curl -v` para conferir. A tela administrativa mostra apenas disponibilidade, sem revelar valores. O botão **Verificar chave e provedor** faz consulta autenticada, sem criar cobrança.

## Webhook FastFlow

1. No painel FastDePix/FastFlow, configure um webhook **ativo** para:

   **`https://pontodecoletaadl.servehttp.com/webhooks/fastflow`**

2. Selecione `transaction.created`, `transaction.approved`, `transaction.paid`, `transaction.expired` e `transaction.refunded`. A API registra por `/webhooks/register`, mas a criação deve ser feita no painel com a conta correta; a aplicação não sobrescreve webhooks existentes. A renovação continua exigindo nova ação do cliente.
3. Copie o segredo do **primeiro webhook ativo** para `FASTFLOW_WEBHOOK_SECRET` localmente na Oracle. Sem webhook ativo, a documentação diz que callbacks individuais podem vir sem assinatura; a aplicação os rejeita. Não liberar callbacks sem HMAC para contornar a configuração.
4. Cada criação envia também `notification_url` exclusiva:
   `https://pontodecoletaadl.servehttp.com/webhooks/fastflow/pedidos/<UUID_DO_PEDIDO>`.
   Libere essa família de caminhos HTTPS no proxy/firewall, sem login ou redirecionamento, preservando corpo e `X-Webhook-Signature`.
5. O provedor envia `X-Webhook-Signature: sha256=<hex>` calculada com HMAC-SHA256 do **corpo original**. A aplicação confere em tempo constante, limita o corpo a 64 KB e só essa rota dispensa CSRF de navegador. Mantém CSRF nas ações humanas.
6. Suporta payload direto e `{event,data}`. Um objeto JSON assinado com `"test": true` recebe `200` imediatamente, mesmo sem transação; não consulta API ou banco financeiro, não cria pedido, ciclo, crédito ou evento financeiro. Assinatura inválida continua `401`; JSON inválido/não objeto continua `400`. Somente o booleano JSON `true` aciona essa verificação, após HMAC. Callback individual e global podem duplicar: a aplicação deduplica e a concessão também é única no banco. Nunca usar a ausência de duplicata no callback como única proteção financeira.
7. Falha temporária da API deixa evento pendente persistido e responde `202`; a reconciliação o recupera. Assinatura inválida recebe `401`; corpo inválido `400`; excessivo `413`. Não há endpoint para administrador marcar pagamento como pago.

## Cartão: requisitos antes da ativação

A documentação fornecida exige enviar PAN, validade e CVV diretamente a `POST /card-in/charges`; **não apresenta checkout hospedado/tokenização**. Por isso, cartão inicia desligado. Pix pode operar sem ativar cartão.

1. Solicite à FastFlow a habilitação de **Card In** para a conta/chave utilizada. GET `/card-in/fees` deve permitir acesso (403 indica indisponível). Confirme condições, tarifas, antifraude e recebíveis com o provedor; não se promete isenção de taxas ou liquidação imediata.
2. Antes de definir `FASTFLOW_CARD_PCI_READY=1`, avalie com o adquirente/provedor e profissional qualificado o escopo e os controles **PCI DSS** aplicáveis à captura direta. Não presumir enquadramento SAQ A ou certificação pelo simples fato de não armazenar cartão. Se disponibilizarem checkout hospedado/tokenização, preferir uma integração nova documentada antes da ativação.
3. Exigir HTTPS válido em toda a cadeia, acesso mínimo ao servidor, atualizações, controles de rede e monitoramento adequados. Confirmar que Nginx/Gunicorn/APM, rastreadores e ferramentas de erro **não capturam corpos de requisição, campos ou respostas sensíveis**; não ativar debug HTTP de `requests`/`urllib3`. Não carregar scripts de terceiros que capturem o formulário. A aplicação não grava número completo, validade ou CVV em banco, sessão, armazenamento do navegador, eventos ou mensagens; só admite bandeira e quatro últimos dígitos no histórico. Ainda assim, PAN/CVV transitam na memória do processo durante a requisição.
4. Depois da avaliação, configure **ambas** `FASTFLOW_CARD_ENABLED=1` e `FASTFLOW_CARD_PCI_READY=1`, reinicie e valide em ambiente de homologação autorizado pelo provedor. O formulário faz pagamento à vista, `installments=1`; não há armazenamento nem reutilização de cartão. Não usar cartões reais nos testes automatizados.
5. Se a conta recusar o recurso ou ocorrer falha, o Pix continua disponível. `402` é recusa, não pagamento. Não há nova cobrança automática após recusa ou timeout.

## Implantação em `ponto-coleta.service`

Após revisão e merge, faça backup/snapshot do banco pelo procedimento já adotado. Não execute testes no banco de produção.

Primeiro confira **localmente** a pasta, usuário e executável reais do serviço, sem publicar a saída caso ela contenha dados privados:

```bash
sudo systemctl show ponto-coleta.service -p WorkingDirectory -p User -p Group -p ExecStart -p EnvironmentFiles
```

Os exemplos de unidades fornecidos usam `/opt/ponto-coleta`, `.venv/bin/python` e usuário/grupo `ponto-coleta`. **A instalação Oracle não foi acessada:** ajuste esses valores nas cópias das unidades conforme o comando acima antes de instalar. Abaixo, entre na pasta real do checkout:

```bash
cd /opt/ponto-coleta
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
env -u DATABASE_URL -u DATABASE -u TEST_DATABASE_URL .venv/bin/python -m pytest -q
sudoedit /etc/ponto-coleta/site.env
```

Se houver mudanças locais, preserve-as antes de atualizar. Confirme que `ponto-coleta.service` carrega `/etc/ponto-coleta/site.env`. Se não carregar, acrescente usando `sudo systemctl edit ponto-coleta.service`:

```ini
[Service]
EnvironmentFile=/etc/ponto-coleta/site.env
```

Cadastre o webhook e seu segredo antes de mudar `FASTFLOW_ENABLED=1`. Deixe os dois controles de cartão em `0` até concluir o procedimento anterior. Não altere as variáveis Google/Gmail existentes.

```bash
sudo systemctl daemon-reload
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
curl --fail --silent --show-error https://pontodecoletaadl.servehttp.com/health
```

A inicialização cria as novas tabelas idempotentemente. Em múltiplos processos, execute a inicialização do schema de forma sequencial no procedimento de atualização antes de subir novos workers, como no restante do sistema. O banco deve ser o mesmo para web e jobs. Nunca apontar `TEST_DATABASE_URL` para produção.

## Agendamentos Oracle

Arquivos revisáveis em `deployment/systemd/`:

- `ponto-coleta-plan-reconcile.service` + `.timer`: a cada minuto após boot; consulta pendentes, recupera eventos, verifica pagos periodicamente para estornos perdidos e disponibiliza ciclos devidos, sem criar cobranças. O timer monotônico volta no boot; os pedidos continuam no banco durante indisponibilidade.
- `ponto-coleta-plan-reminders.service` + `.timer`: 08h00 em `America/Sao_Paulo`, `Persistent=true`; recupera execução perdida ao voltar. A reserva ciclo/dia evita reenvio, inclusive se houver disparos sobrepostos. O horário exato de validade continua explícito no e-mail.

Ajuste os caminhos/usuário/grupo **nas cópias** antes de habilitar:

```bash
sudo install -m 644 deployment/systemd/ponto-coleta-plan-reconcile.service /etc/systemd/system/
sudo install -m 644 deployment/systemd/ponto-coleta-plan-reconcile.timer /etc/systemd/system/
sudo install -m 644 deployment/systemd/ponto-coleta-plan-reminders.service /etc/systemd/system/
sudo install -m 644 deployment/systemd/ponto-coleta-plan-reminders.timer /etc/systemd/system/
sudoedit /etc/systemd/system/ponto-coleta-plan-reconcile.service
sudoedit /etc/systemd/system/ponto-coleta-plan-reminders.service
sudo systemctl daemon-reload
sudo systemctl enable --now ponto-coleta-plan-reconcile.timer ponto-coleta-plan-reminders.timer
sudo systemctl list-timers 'ponto-coleta-plan-*' --no-pager
```

Para uma execução sob as mesmas variáveis protegidas:

```bash
sudo systemctl start ponto-coleta-plan-reconcile.service
sudo systemctl start ponto-coleta-plan-reminders.service
sudo systemctl status ponto-coleta-plan-reconcile.service ponto-coleta-plan-reminders.service --no-pager
```

**O segundo comando envia lembretes reais elegíveis quando executado em produção.** Não o use como teste de homologação sobre o banco real. Não cria cobranças. Mantenha as variáveis Gmail já existentes. Logs das rotinas mostram contagem/erro genérico, sem credenciais, códigos ou corpos de pagamento. O painel permite conferir estados de envio; aceitação da Gmail API não garante chegada à caixa de entrada.

## Conferência administrativa e pendências de produção

Acesse **Área do Ponto → Configurações → Plano ME GUARDE Mais** como administrador com permissão de configurações (ou acesso administrativo legado habilitado). Há pedidos, clientes, IDs, valor, estados, recebíveis, datas, ciclos, concessões, consumo e notificações sanitizadas. **Consultar na API** não altera status remotamente nem concede por decisão humana. Cliente: **Área do Cliente → Meu plano e créditos**, endereço `/cliente/plano`.

Antes de operar: configurar chave fastflow e validar provedor, cadastrar webhook/segredo correto e testar entrega HTTPS, confirmar Gmail e timers, revisar o tratamento de dados da FastFlow na Política de Privacidade e as regras com o responsável, e completar a avaliação PCI/liberação do Card In se desejar cartão. A política foi complementada com o novo tratamento condicional e mantém os pontos de revisão já existentes. Nenhuma ativação real ou implantação Oracle foi executada por esta tarefa.

Não há sandbox documentado no material entregue. Uma validação ponta a ponta real depende de ambiente autorizado do provedor e das credenciais no servidor; os testes locais não comprovam disponibilidade da conta, entregabilidade Gmail, operação do proxy ou liquidação real. Não são feitas cobranças reais para substituir essa homologação.

## Verificação desta entrega

**292 testes aprovados** na execução completa com SQLite temporário e PostgreSQL 16 em banco novo isolado. Cobrem confirmação válida, `approved` sem crédito, valor/provedor/cliente divergentes, HMAC inválido, duplicação e ordem dos eventos, webhook anterior à resposta de criação, timeout sem novo POST, reconciliação sem webhook, ciclo mensal e renovação antecipada, substituição de renovação estornada, cartão recusado e recebível agendado, acesso por senha/Google, restrição administrativa, consumo concorrente e ausência de nova cobrança base na retirada, limites compartilhados de API, lembretes idempotentes e persistência após reinicialização. O envio de e-mail e a API são substituídos exclusivamente nas fixtures.

Chromium: plano em **320, 360, 390, 430, 768, 1024 e 1440 px**, pagamento Pix e painel administrativo em telas pequenas e grandes, formulário de cartão, contexto móvel com toque no botão de cópia, ativação e renovação antecipada. Sem rolagem horizontal da página e sem erros JavaScript nos cenários verificados. O teste reproduzível está em `tests/browser/plan_fastflow.py`; capturas ficam em `/tmp`, não contêm dados reais. Não foram usados aparelhos físicos ou Safari/iOS.
