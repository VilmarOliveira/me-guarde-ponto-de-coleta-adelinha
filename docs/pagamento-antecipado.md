> Integração removida. As instruções de provedor deste documento são históricas e não devem ser executadas. Use [a remoção e os pagamentos locais](remocao-fast.md).

# Pagamento antecipado de pacotes

Cliente: **Área do Cliente → Meus pacotes → selecionar pacotes → Pagar antes da retirada**. Confere o saldo e gera o Pix Fast. QR Code, Copia e Cola, validade e consulta ficam na página própria, restrita ao titular. Créditos de plano e preços registrados no recebimento não são alterados.

Equipe: selecione os pacotes na Área do Ponto, escolha Pix Fast e utilize **Gerar pagamento antecipado**. A modalidade existente **Gerar Pix Fast** continua disponível para a entrega no fluxo tradicional. Cliente e equipe compartilham reservas: seleção idêntica reaproveita o pedido; seleção parcialmente sobreposta é bloqueada. Uma cobrança existente conserva seus ajustes e valores. A criação não é repetida automaticamente em caso de timeout.

O cliente não pode enviar preços, acréscimos ou descontos próprios. O servidor calcula o saldo e reutiliza os ajustes de cobranças existentes; novos ajustes manuais continuam exclusivos da equipe. O Pix Fast antecipado aceita R$ 4,00 a R$ 1.000,00, inclusive. Saldo zero não cria cobrança; fora da faixa positiva, paga-se na retirada. A prévia exibe cobertura do plano e eventuais diárias separadamente. Mudança no total entre prévia e geração exige nova conferência.

Somente pagamento efetivo consultado à FastFlow e validado no servidor quita valores. `approved` isolado, retorno do navegador ou imagem de comprovante não quitam. Cada quitação grava valor financeiro distribuído em centavos por pacote e a parcela de recebimento/atraso liquidada. Essa separação preserva acréscimos/descontos efetivamente aplicados, sem cobrar novamente o desconto nem transformar um acréscimo pago em crédito para futuras diárias. O arredondamento preserva exatamente o total da transação.

O registro financeiro não entrega pacotes. O aviso original e o prazo permanecem intactos: novas diárias geram somente saldo adicional. Saldo zero dispensa nova cobrança; documento, identidade, assinatura e confirmação da entrega continuam obrigatórios. Retiradas parciais preservam a quitação de cada pacote. Histórico e novos PDFs identificam o valor antecipado separadamente do saldo pago na retirada. Documentos anteriores não são regravados.

Estorno, análise ou pagamento tardio após cancelamento seguem para conferência; não autorizam entrega automática. A quitação é idempotente por transação/pacote, com travamento dos pacotes compartilhado pela criação de cobrança, confirmação de pagamento e entrega.

## Implantação

Sem novas variáveis, credenciais ou agendamentos na Oracle. Use o procedimento habitual de backup, atualização e reinício de `ponto-coleta.service`, com inicialização sequencial do schema antes de iniciar múltiplos workers. A alteração acrescenta `package_prepayments`, um índice por pacote, o indicador `advance` do pedido e o valor antecipado no registro da retirada. O migrador SQLite → PostgreSQL inclui a nova tabela. O job e os webhooks autenticados existentes conciliam as cobranças; os callbacks continuam em `/webhooks/fastflow/retiradas/<UUID>`. Não foi realizada implantação na Oracle.

## Verificação

Testes fictícios e API simulada: cliente/equipe, reservas e sobreposição, limites, pacote coberto, quitação por pacote, eventos repetidos, saldo adicional, descontos, pagamentos sucessivos, troca de titular, HMAC inválido, valor divergente e estorno. Testes de navegador em `tests/browser/advance_payments.py`, nas larguras de 320, 360, 390, 430, 768, 1024 e 1440 px. Não são enviadas cobranças ou mensagens reais. A emulação Chromium não substitui aparelhos físicos/Safari.

Resultados: suíte geral com 373 aprovados e 14 testes dependentes de ambientes opcionais não executados; rodada final focada com 96 aprovados e 1 não executado, incluindo PostgreSQL isolado com três emissões concorrentes para os mesmos pacotes, um único Pix, quitação idempotente, retirada zero e PDF. Chromium passou nas sete larguras, sem erros JavaScript ou rolagem horizontal. As contagens das rodadas se sobrepõem.
