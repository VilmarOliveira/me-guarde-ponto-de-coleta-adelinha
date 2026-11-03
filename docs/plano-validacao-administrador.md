# Plano: ativação, renovação e cancelamento pelo administrador

Acesso: **Área do Ponto → Configurações → Plano ME GUARDE Mais**. As ações exigem administrador ativo com permissão de configurações; as validações também ocorrem no servidor. Cadastro, assinatura, aceite, confirmação por e-mail e autorizações do cliente continuam obrigatórios conforme as regras existentes.

## Ativar ou renovar

1. Busque o cliente por nome, ID ou e-mail e abra **Validar participação** (ou a ação da solicitação existente).
2. Confira cliente, ID, pedido, R$ 33,90, 10 créditos e período previsto.
3. Escolha exatamente uma modalidade:
   - **Pix AI:** QR Code/Copia e Cola da chave CNPJ configurada. Confira o recebimento na conta, marque a confirmação e utilize **Confirmar pagamento e ativar plano**. Não consulta a API.
   - **Dinheiro:** confirme o recebimento e utilize o mesmo botão. Não consulta a API.
   - **Pix Fast:** **Gerar Pix Fast** cria ou reutiliza uma cobrança. Acompanhe QR Code, validade e situação, com **Consultar pagamento**. Somente pagamento efetivo confirmado pela API libera o plano; não há substituição por confirmação manual.
4. Informe o motivo e confirme a operação. Voltar não grava nada.

O primeiro ciclo começa na confirmação do pagamento. Renovação antecipada preserva o ciclo atual e reserva o próximo sem antecipar créditos. Há proteção contra repetição e concorrência. Créditos são consumidos no recebimento, nunca novamente na retirada. Uma cobrança FastFlow pendente precisa ser resolvida antes de mudar a modalidade.

Novos recebimentos manuais registram modalidade, valor, administrador e `manual_paid_at`, separados de `paid_at` do provedor. O administrador não preenche `accepted_at` em nome do cliente. Validações administrativas históricas sem recebimento registrado permanecem identificadas como tais, sem serem transformadas em pagamentos. Novos comprovantes PDF ficam disponíveis ao cliente titular e ao administrador.

## Cancelar contratação não paga

Use o filtro de solicitações não pagas e o botão existente **Cancelar solicitação**. Confira cliente e pedido, informe o motivo e confirme.

- Sem transação e com evidência de que a criação não ocorreu, encerra somente o pedido local.
- Havendo cobrança, consulta e valida os dados. Se paga ou com ciclo liberado, recusa o cancelamento simples.
- Para cobrança aberta, utiliza o método documentado `PUT /transactions/{id}`, com `status=cancelled`, e consulta novamente. O fluxo anterior utilizava DELETE; a nova chamada não pressupõe autorização da conta do provedor.
- Consulta indisponível, histórico incerto ou recusa remota mantém **Cancelamento aguardando verificação**. O pedido não é apresentado como cancelado com sucesso. A ação existente permite verificar novamente; o job também acompanha a situação.

O histórico registra administrador, data, motivo e resultado. Falhas HTTP incluem apenas método, endpoint e código HTTP, sem credenciais ou corpo da resposta. A causa HTTP exata da instalação Oracle não pôde ser observada neste ambiente; esses diagnósticos permitem identificá-la na próxima tentativa. Recusa de permissão no provedor exige conferência da conta FastFlow.

Pagamento posterior ao cancelamento fica sinalizado para análise (`payment_after_cancellation`), sem ativação automática nem novos créditos. Pagamento externo adicional após confirmação manual fica sinalizado como possível duplicidade, sem novo ciclo. Não há estorno automático, exclusão ou reescrita de comprovantes históricos.

## Implantação e validação

Consulte [Área do Cliente e pagamentos](area-cliente-pagamentos.md) para implantação, testes e limites de homologação. Não há novas credenciais ou agendamentos.
