# Revisão antes de reconfigurar a integração Fast

**Estado da entrega:** cartão corrigido e preparação/testes locais concluídos. Não houve acesso, backup, exclusão, troca de credenciais, cobrança real ou validação bancária na Oracle. Não é possível fornecer contagens reais de produção a partir deste ambiente. O utilitário abaixo gera a lista exata para revisão na instalação, sem chamar a Fast nem alterar registros. Não possui opção de aplicar limpeza.

## Proposta para revisão

| Conjunto | Ação proposta |
| --- | --- |
| Eventos `plan_payment_events` explicitamente identificados como `test`, `integration.test` ou `transaction.test`, já processados/ignorados, **sem pedido e sem ID de transação** | Candidatos a exclusão, enumerados por ID no relatório; revisar antes de executar qualquer limpeza. |
| Contadores `plan_api_budget` com mais de 24 horas | Candidatos a limpeza técnica; manter os recentes, que limitam chamadas à Fast. |
| Eventos de webhook ligados a pedido/transação, eventos pendentes, marcadores de deduplicação e registros de origem desconhecida | Preservar. Um nome parecido com “teste” não basta para excluir. |
| Pedidos, pagamentos, comprovantes, pacotes, retiradas, planos, ciclos, créditos, reservas, antecipações, decisões administrativas e auditoria financeira | Preservar integralmente. Nenhuma tabela financeira integra a limpeza proposta. |
| Cobranças abertas, em análise, com erros ou transação externa sem pagamento registrado | Inventariar e conferir; não cancelar, apagar, liberar reservas ou gerar substitutas automaticamente. Arquivadas também entram na revisão. |
| Transação `127471` | Destacar para conferência, qualquer que seja o estado local; HTTP 403 não confirma pagamento, cancelamento ou expiração. Não foi consultada de verdade nesta entrega. |
| Logs gerais de sistema/proxy | Preservar nesta etapa. Podem conter evidências financeiras; não há classificação segura para exclusão automática. |
| Configuração Fast antiga | Fazer cópia privada e substituir somente os valores da integração após homologação e análise das cobranças antigas. Não apagar `site.env` nem configurações de Gmail/Google, Pix AI ou dinheiro. |

Testes de webhook atuais (`test: true`) são aceitos após HMAC e não gravam eventos financeiros. Assim, pode não haver eventos elegíveis para excluir; o relatório não inventa candidatos. Uma tentativa de cobrança sem confirmação também **não** é um teste descartável.

## 1. Preparar backup e inventário

Revisar o serviço e sua pasta real:

```bash
sudo systemctl show ponto-coleta.service -p WorkingDirectory -p User -p EnvironmentFiles
```

Usar o nome efetivo se a instalação utilizar `me-guarde.service`. Preparar uma pasta privada fora do repositório, acessível apenas ao operador/usuário do serviço. Fazer cópia protegida de `/etc/ponto-coleta/site.env` e do procedimento de implantação/proxy pelo método habitual. Não enviar essas cópias ao Git, chat ou navegador.

Executar com o Python e o **mesmo ambiente protegido do serviço**; uma sessão SSH comum pode não carregar `DATABASE_URL`. Não passar URI com senha na linha de comando, não executar `cat site.env` nem imprimir o ambiente. Com manutenção coordenada e sem alterações concorrentes no banco, na pasta da aplicação:

```bash
.venv/bin/python scripts/review_fast_integration.py \
  --backup-dir /CAMINHO_PRIVADO/backup-fast \
  --output /CAMINHO_PRIVADO/revisao-fast.json
```

Os caminhos são exemplos a substituir. O script não importa/inicializa o aplicativo nem aplica migrações. Abre a origem para leitura, faz backup consistente do SQLite ou usa `pg_dump` em formato customizado para PostgreSQL, cria arquivos novos com permissão `0600` e registra SHA-256, tamanho e caminho. A senha PostgreSQL é passada pelo ambiente do subprocesso, não pelos argumentos. Exige `pg_dump` compatível com o servidor. Qualquer falha interrompe a preparação sem limpeza.

O relatório contém contagens das tabelas preservadas, IDs de candidatos técnicos e pedidos a conferir (tipo, cliente por ID, estado local, valor, transação e erro). Não contém chaves, segredos, tokens, CPF, e-mail ou QR Code. Não faz consultas remotas, portanto distingue estado local de status remoto não consultado. Também registra que o backup **ainda não foi restaurado para teste**. Validar restauração em uma base isolada antes de autorizar exclusões. Durante operação concorrente, backup e inventário podem corresponder a instantes diferentes; repetir na janela coordenada.

**Ponto de revisão:** apresentar o relatório e a confirmação da restauração ao responsável. A limpeza e a troca das configurações na Oracle ficam pendentes dessa revisão. Nenhum comando de exclusão é executado pelo utilitário.

## 2. Pausar somente novas cobranças Fast

Foi acrescentada a opção `FASTFLOW_NEW_CHARGES_ENABLED`, com padrão `1` para preservar o funcionamento atual. Durante revisão, configurar `0` no ambiente do serviço, conservando `FASTFLOW_ENABLED=1`, chave e segredo atuais válidos. Após reinício controlado:

- novas cobranças Fast ficam pausadas na seleção de modalidade e no adaptador HTTP;
- “Pix não funcionou” não cancela nem substitui o pedido durante essa pausa;
- consultas, webhooks e conciliação das cobranças existentes continuam habilitados;
- novas cobranças podem oferecer Pix AI ou dinheiro segundo a prioridade administrativa;
- cobranças já emitidas conservam sua modalidade e valor, sem troca automática.

Não usar `FASTFLOW_ENABLED=0` para esse objetivo: essa variável também desabilita a recepção normal dos webhooks. Preservar os jobs existentes. Antes de migrar/remover uma credencial, comprovar que a nova pode consultar todas as transações antigas vinculadas; se retornar 403, interromper a troca e solicitar conferência ao provedor.

## 3. Reconfigurar com credenciais fornecidas pelo responsável

Valores privados devem ser inseridos diretamente no arquivo protegido da Oracle, sem passar pelo repositório ou pelos logs. Preparação da configuração:

```dotenv
FASTFLOW_ENABLED=1
FASTFLOW_NEW_CHARGES_ENABLED=0
FASTFLOW_API_KEY=<nova chave da mesma conta Fast autorizada>
FASTFLOW_WEBHOOK_SECRET=<segredo de assinatura confirmado com a Fast, com pelo menos 32 caracteres>
FASTFLOW_PUBLIC_ORIGIN=<origem HTTPS atual, sem caminho ou barra final>
FASTFLOW_CARD_ENABLED=0
FASTFLOW_CARD_PCI_READY=0
```

Não copiar os marcadores `<...>` literalmente. O segredo deve coincidir com o utilizado pelo provedor; não basta gerar um segredo só na aplicação. Não revogar a chave/segredo anterior até confirmar a continuidade das consultas e notificações das cobranças antigas. Se ainda houver notificações assinadas com o segredo antigo, manter esse segredo até resolver a migração com o provedor. A entrega não implementa uma troca automática entre contas Fast nem aceitação indiscriminada de dois segredos.

Reaproveitar o adaptador existente validado, a base `https://fastdepix.space/api/v1/` e as URLs individuais que já vinculam as transações aos pedidos:

- `/webhooks/fastflow/pedidos/<id>` para plano/renovação;
- `/webhooks/fastflow/retiradas/<id>` para cobranças de pacotes;
- `/webhooks/fastflow` para o callback global, conforme a configuração existente.

Na origem usada anteriormente, esses caminhos ficam sob `https://pontodecoletaadl.servehttp.com`; confirmar o domínio vigente antes de configurar. Não trocar os callbacks gravados em cobranças antigas. Manter HTTPS, validação HMAC, limite do corpo, vínculo por ID/callback, conferência de valor/provedor e deduplicação. Teste assinado `test: true` deve retornar 200; assinatura inválida deve ser recusada. Essa etapa não comprova pagamento ou validade bancária do QR.

## 4. Homologação real após revisão

Somente com credenciais válidas e autorização operacional, habilitar novas cobranças (`FASTFLOW_NEW_CHARGES_ENABLED=1`), reiniciar o serviço e conferir:

1. Primeira assinatura: gerar um Pix novo com cliente fictício/autorizado, conferir o valor vigente e abrir o QR/Pix Copia e Cola no banco. A aceitação pelo aplicativo bancário deve ser verificada **manualmente**, sem declarar sucesso a partir de mocks. Qualquer pagamento real deve ser controlado e autorizado.
2. Renovação: repetir com ciclo vigente, preservando seu fim e disponibilizando o próximo ciclo no momento correto.
3. Antecipado: repetir com pacotes aguardando retirada, preservar o prazo e validar abatimento após confirmação pela API.
4. Pagamento: consulta e webhook devem liberar o efeito financeiro uma única vez, inclusive com notificações/cliques repetidos.
5. Expiração: esconder o QR vencido; não criar outra cobrança enquanto houver estado pendente/incerto ou falha de consulta. Gerar substituta apenas após encerramento sem pagamento confirmado.
6. Conferir Pix AI, dinheiro e cobranças antigas; depois reconciliar pela rotina já configurada, que não cria cobranças. Não usar a rotina de lembretes como teste, pois ela pode enviar e-mails reais.

## Verificação local

Teste automatizado cobre o inventário sem alteração de dados, destaque da 127471, proteção dos eventos ligados/indeterminados, backup SQLite restaurável e privado, isolamento da senha do backup PostgreSQL, pausa de criação com continuidade dos webhooks e os estados pago/expirado nos três fluxos, com repetição das notificações. Leitura do inventário PostgreSQL foi exercitada em banco local de testes. APIs e pagamentos são simulados: isso não comprova aceitação bancária, permissão da conta Fast ou operação da Oracle.

No navegador, foram verificadas 320, 360, 390, 430, 768, 1024 e 1440 px: ausência do cartão no site público/login/após sair, inclusive ao visitar página pública autenticado; contador e destino preservados dentro do cliente; gestão da equipe separada e Pix AI disponível durante a pausa da Fast. Não houve limpeza em produção.
