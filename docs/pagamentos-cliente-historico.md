> Integração removida. As instruções de provedor deste documento são históricas e não devem ser executadas. Use [a remoção e os pagamentos locais](remocao-fast.md).

# Economia, histórico e pagamentos do cliente

## Comparativo

**Área do Cliente → Meu plano** exibe o comparativo antes do ciclo atual. Valores representam uso completo de R$ 33,90: 10 pequenos ou 5 grandes, ou combinação equivalente. É informativo: mensalidade e cobrança por pacote não mudam.

## Retiradas

A coluna Retirados mostra duas retiradas completas, ordenadas pela data efetiva da entrega, independentemente da paginação de pacotes. Os detalhes reutilizam o comprovante existente, incluindo todos os pacotes daquela retirada. O histórico inicia em Hoje, no fuso `America/Sao_Paulo` de Franca. Mês/ano, dia, período inclusivo e todas as retiradas usam a data da entrega, não a chegada. Limpar filtros retorna a Hoje. Acesso exige permissão de retiradas, inclusive nas rotas diretas.

Datas históricas com offsets diferentes são normalizadas antes da comparação. Os registros anteriores e comprovantes não são regravados.

## Configuração

**Área do Ponto → Configurações → Pagamentos do cliente**, somente administrador. Há três abas independentes: Pagamento antecipado, Contratação do plano e Renovação do plano. Cada uma tem faixas inclusivas, ativação, prioridades distintas 1/2/3 e textos próprios. A prévia usa o formulário sem salvar nem chamar API; salvar altera somente aquela aba.

Inicialmente: Pix Fast R$ 4,00–R$ 1.000,00; Pix AI R$ 0,00–R$ 100.000,00; dinheiro por último. A seleção aplica a interseção da faixa configurada com as restrições técnicas da integração FastFlow (R$ 4,00–R$ 5.000,00). Pode-se diminuir ou ampliar a faixa configurada, mas nenhuma cobrança FastFlow sai dos limites técnicos. Pix AI depende da chave CNPJ e titular configurados. Zero resulta em Nada a pagar, sem cobrança. Com todos os meios inelegíveis, o sistema orienta procurar o ponto.

Só uma opção é apresentada. Exemplos: máximo Fast de R$ 500,00 no antecipado faz R$ 600,00 seguir para Pix AI quando elegível; não altera as abas do plano. Se Pix não estiver disponível, dinheiro habilitado aparece como Pagar em dinheiro no ponto. A seleção nunca modifica o saldo ou a mensalidade.

### Cobranças existentes e confirmação

Cada nova cobrança conserva a modalidade e uma cópia da regra/textos. Pedidos pendentes são reaproveitados sem trocar de modalidade, mesmo depois de editar ou desabilitar regras. Criação incerta exibe Verificando cobrança. Timeout não repete POST nem provoca fallback para pagamento manual. Uma criação Fast definitivamente recusada, sem transação externa identificada, permite fallback em nova tentativa; a indisponibilidade por cliente/tela é considerada por cinco minutos. Não se presume falta de pagamento quando a consulta é incerta.

FastFlow continua exigindo pagamento efetivo confirmado pelo servidor. Cliente não pode marcar seu pedido como pago. Para Pix AI ou dinheiro antecipado, a equipe abre a cobrança pela Área do Ponto, confere o recebimento e usa Confirmar pagamento recebido. Somente então é registrada a quitação por pacote. Idempotência, reservas e conferência física da entrega são mantidas. É possível cancelar localmente uma cobrança manual ainda não paga pelo controle existente.

No plano, a solicitação manual aparece no painel existente de **Plano ME GUARDE Mais**; o administrador confere o recebimento e usa a ativação/renovação existente na modalidade registrada. Cadastro, regras, assinatura e autorizações continuam obrigatórios; confirmação administrativa não substitui o aceite do cliente. Renovação antecipada reserva o próximo ciclo sem antecipar créditos. Pix Fast pendente não aceita confirmação manual para contornar a API.

## Oracle

Não há novas variáveis, credenciais, bibliotecas ou agendamentos. Faça backup habitual, revise/mescle a branch, atualize o checkout e reinicie o serviço existente:

```bash
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip check
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

A inicialização acrescenta a tabela `customer_payment_policies` e campos de modalidade/regra congelada em pedidos existentes, sem alterar o conteúdo de cobranças antigas. Inicialize o schema sequencialmente antes de subir múltiplos workers. Defaults são inseridos apenas quando ausentes; reinícios não sobrescrevem configurações. O migrador SQLite → PostgreSQL copia configurações sobre defaults iniciais; divergências com configurações já personalizadas no destino interrompem a migração para conferência, sem sobrescrevê-las silenciosamente.

A Oracle não foi acessada. APIs e e-mails reais não são utilizados nos testes. A emulação Chromium não substitui aparelhos físicos/Safari.

## Resultado da validação

409 testes aprovados na suíte completa, com SQLite e PostgreSQL isolados, API simulada e sem cobranças ou e-mails reais. Cobertura inclui faixas inclusivas, prioridades, independência entre abas, prévia sem cobrança, reinício/persistência, permissões, regra congelada de pedidos pendentes, falha definitiva versus criação incerta, conferência manual, renovação antecipada e filtros no fuso local. Chromium validado em 320, 360, 390, 430, 768, 1024 e 1440 px, sem erros JavaScript ou rolagem horizontal.
