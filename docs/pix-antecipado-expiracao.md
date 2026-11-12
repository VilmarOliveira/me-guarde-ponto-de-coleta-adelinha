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

Escopo: pagamentos de pacotes, incluindo pagamento antecipado e balcão.
As regras de contratação e renovação de planos permanecem iguais.

Validação automatizada: renovação com API ainda pendente, botão e conferência
do cliente, reutilização da nova cobrança, resposta paga, pagamento tardio,
consulta indisponível, identidade divergente, prazo futuro/inválido, QR Code
diferente sem validade e estados `approved`/`under_review`. Nenhuma cobrança
real é gerada pelos testes.
