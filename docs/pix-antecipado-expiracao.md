# Renovação do Pix dos pacotes

A Fast pode continuar retornando `pending` depois do vencimento do QR Code.
Ao consultar o pagamento, o sistema verifica a identidade e o valor da cobrança
pela API. Se ela continuar pendente e o prazo informado pela Fast já terminou,
encerra a reserva local dos pacotes e libera uma nova solicitação. O botão
`Gerar novo Pix` leva à conferência atualizada dos valores antes da criação.

Esse encerramento é local: não solicita nem declara cancelamento na Fast.
Um Pix ainda válido é reutilizado. Uma resposta paga prevalece sobre a data de
vencimento. Falhas, respostas inconsistentes, situações em análise ou ausência
de uma validade confiável mantêm a reserva. Quando a API omite a validade,
somente o prazo salvo para o mesmo QR Code pode ser utilizado.

O histórico da cobrança anterior permanece. Qualquer pagamento tardio dessa
cobrança recebe `late_payment_review`, sem quitar automaticamente os pacotes
nem interferir na reserva de uma cobrança nova. As consultas continuam sujeitas
ao intervalo existente de 20 segundos para evitar chamadas simultâneas.

Escopo: pagamentos de pacotes, incluindo pagamento antecipado e balcão, e
Pix de contratação/renovação do plano. Todos consultam a API antes de encerrar
localmente um QR Code vencido. A nova tentativa do plano preserva o histórico,
o vínculo com a cobrança anterior e o valor da tentativa original. Pagamentos
tardios do plano recebem `payment_after_cancellation` para análise, sem ativar
um ciclo ou conceder créditos automaticamente.

Validação automatizada: renovação com API ainda pendente, botão e conferência
do cliente, reutilização da nova cobrança, resposta paga, pagamento tardio,
consulta indisponível, identidade divergente, prazo futuro/inválido, QR Code
diferente sem validade e estados `approved`/`under_review`. Nenhuma cobrança
real é gerada pelos testes.

## Modalidade ao solicitar outro Pix

O botão `Gerar novo Pix` identifica a cobrança expirada e preserva a intenção
Pix Fast na conferência e na criação. A prioridade de Dinheiro ou Pix AI não
substitui silenciosamente essa escolha. O fluxo comum de pagamento continua
seguindo a ordem configurada no painel.

A nova solicitação respeita a habilitação, os limites e a disponibilidade atual
do Pix Fast. Se não for elegível, informa a indisponibilidade sem criar cobrança
em dinheiro. O servidor valida o cliente, os pacotes e a situação da cobrança
original, e confere novamente a elegibilidade na criação.
