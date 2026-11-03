# Validação manual e cancelamento de solicitações do plano

Acesso: **Área do Ponto → Configurações → Plano ME GUARDE Mais**. Disponível somente a administrador ativo com permissão de configurações, ou ao acesso administrativo legado enquanto habilitado. Cliente, gerente e atendente não podem executar as ações, inclusive por POST direto.

## Validar a participação de um cliente

1. Em **Validar participação de clientes**, busque por nome, ID ou e-mail. A lista é paginada e permite localizar todos os clientes cadastrados, mesmo sem pedido de plano anterior.
2. Clique em **Validar participação**. Para uma solicitação já existente, também é possível usar **Validar manualmente** na linha do pedido.
3. Confira cliente, pedido, ciclo atual e efeito da validação. Informe um motivo de 10 a 600 caracteres, marque a confirmação e clique em **Confirmar validação manual**. **Voltar sem alterar** não grava nada.
4. A primeira participação começa na validação. Se há ciclo vigente, a validação será uma renovação a partir do fim dele; os 10 créditos do próximo ciclo não ficam disponíveis antecipadamente. Se o próximo ciclo já está confirmado, não é criado outro. Créditos não acumulam e continuam sendo consumidos somente no recebimento; pacotes anteriores não são reprecificados.

A participação recebe o estado **Participação validada manualmente**, separado de pagamento confirmado na FastFlow. A ação não cria cobrança, não envia dinheiro e não muda o status da transação no provedor. Nos pedidos criados diretamente pelo administrador, a origem é `administrative`, `paid_at` fica vazio e `accepted_at` não é preenchido como se o cliente tivesse aceitado as regras. O valor do plano permanece R$ 33,90 como referência; a decisão não é apresentada como liquidação financeira.

A conta precisa estar ativa e cumprir as validações cadastrais/contratuais já existentes, inclusive pendências que restrinjam a função do plano. O administrador não assina, confirma e-mail ou aceita autorizações pelo cliente. A prévia informa o que precisa ser regularizado; o cliente continua usando o fluxo existente na própria Área do Cliente.

Se já existe solicitação para aquele ciclo, ela é reaproveitada. Reenvios da mesma confirmação e operações concorrentes não duplicam pedidos, ciclos ou créditos: há identificador persistente da operação, travamento por cliente, vínculo único do ciclo e comparação com o ciclo mostrado na prévia. Uma segunda renovação exige uma nova prévia consciente.

Caso a FastFlow confirme depois um pedido validado manualmente, o pagamento e sua data são registrados, mas o ciclo já criado e seus créditos não são duplicados. A decisão administrativa permanece no histórico. Expiração/cancelamento da cobrança ainda não paga não apaga a participação já autorizada; estorno ou nova análise após pagamento continuam sujeitos à revisão prevista no sistema.

## Cancelar uma solicitação não paga

1. Use **Somente solicitações não pagas**, a busca e a paginação de solicitações para localizar o pedido.
2. Clique em **Cancelar solicitação**, confira cliente/transação, informe o motivo, marque a confirmação e clique em **Confirmar cancelamento**.
3. Havendo cobrança conhecida, o servidor consulta os detalhes da FastFlow, com as mesmas verificações de valor, provedor, método e vínculo. Se estiver paga, registra a confirmação real e recusa o cancelamento. Se ainda estiver aberta, usa o endpoint documentado `DELETE /transactions/{id}` e consulta de novo para confirmar `cancelled` ou `expired` antes de encerrar localmente.
4. Se a API estiver indisponível, a situação continuar incerta ou a transação não tiver sido identificada, a solicitação é preservada para nova consulta. Não é declarada cancelada sem confirmação. Tentativas definitivamente recusadas/falhas e sem identificador podem ser encerradas localmente, sem chamadas ao provedor.

O cancelamento recebe estado **Solicitação cancelada pelo administrador** e não apaga o pedido, transação ou evidências. Pedidos pagos, estornados ou com ciclo/participação já liberado não podem ser tratados como “não pagos” para cancelamento. Repetir a confirmação não envia novo cancelamento nem regrava o motivo anterior.

Um pagamento tardio comprovado pela API depois do cancelamento fica registrado para conferência (`payment_after_cancellation`), com a data, mantendo o estado cancelado. Não reabre o plano nem concede créditos automaticamente; a tela orienta o cliente a procurar o ponto. O atendimento deve conferir e resolver o caso, sem estorno automático ou exclusão do histórico. Webhooks antigos/repetidos não revertem essa proteção.

## Histórico e dados preservados

**Histórico das decisões administrativas** mostra cliente, pedido, decisão, administrador identificado, data/hora, motivo e estados anterior/final. Os registros ficam em `plan_admin_decisions`, com referência aos pedidos/clientes e identificação preservada do administrador. Não há ação para editar ou apagar esse histórico.

A alteração é aditiva: cria tabela de decisões e índice de unicidade que considera a participação manual. Mantém dados, contratos, assinaturas, pacotes, pagamentos anteriores e configurações. A retirada de pacotes já recebidos permanece disponível, e o valor base já coberto não volta a ser cobrado. A reconciliação continua usando a API para cobranças reais, e disponibiliza ciclos manuais devidos mesmo se novos pagamentos FastFlow estiverem desabilitados. Renovação manual confirmada também suprime novos lembretes do vencimento anterior.

## Implantação

Não há nova variável, credencial, biblioteca ou timer. Preserve `/etc/ponto-coleta/site.env`, Gmail, Google, FastFlow e os agendamentos da Oracle. Após revisão/merge, backup habitual e atualização do checkout na pasta real do sistema:

```bash
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip check
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

A inicialização cria as estruturas adicionais idempotentemente no banco persistente; como no restante do sistema, faça a inicialização do schema de forma sequencial antes de iniciar novos workers simultâneos. O script de migração SQLite → PostgreSQL inclui o novo histórico. A Oracle não foi acessada nem reiniciada por esta implementação.

## Verificação

**339 testes aprovados** na suíte completa com SQLite e PostgreSQL 16 isolado. Testes com clientes/administradores fictícios, SQLite temporário, PostgreSQL 16 isolado e FastFlow/Gmail simulados: validação com/sem solicitação, origem identificada sem falso pagamento/aceite, restrições cadastrais, primeiro ciclo e renovação, concorrência/duplo envio, confirmação/reason/CSRF, pagamento durante cancelamento, falha de rede, recusa de cancelar pagos, cancelamento confirmado, pagamento tardio, histórico persistente e permissões.

Chromium com telas de 320, 360, 390, 430, 768, 1024 e 1440 px: busca, lista, prévia, confirmação manual, cancelamento por toque e histórico, sem rolagem horizontal da página ou erros JavaScript. A emulação não substitui aparelhos físicos/Safari. APIs e envio de e-mail reais não são usados nos testes.
