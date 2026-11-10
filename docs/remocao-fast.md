# Remoção da integração e dos dados de cobranças Fast

A instrução mais recente autoriza excluir todas as cobranças Fast, inclusive pendentes como 127471, sem mantê-las no histórico e **sem criar backup**. Isso substitui a proposta anterior de revisão/arquivamento. Não há reconfiguração ou nova integração nesta entrega.

## Alterações

- Cliente HTTP, chamadas ao provedor, webhooks, criação/cancelamento/consulta remota, arquivos de configuração do site e unidades de reconciliação exclusivas foram removidos.
- Só Pix AI e dinheiro permanecem nas configurações, assinatura, renovação, antecipação e retirada. A confirmação do pagamento é feita pela equipe autorizada, no servidor; nunca pelo cliente.
- A inicialização executa uma migração transacional e idempotente que exclui os pedidos Fast, seus eventos técnicos, tentativas, reservas pendentes, decisões exclusivas e contadores de API. Não transforma cobranças pendentes em pagas ou canceladas e não cria outra cobrança automaticamente.
- A migração preserva cadastros, contratos, assinaturas, retiradas, preços, Pix AI, dinheiro, ciclos concedidos, consumo/saldo de créditos e valores antecipados já quitados. Para manter as referências dos ciclos e saldos, cria registros financeiros internos sem transação/callback/QR do provedor. Eles não aparecem na lista de cobranças.
- Comprovantes financeiros anteriores continuam acessíveis; documentos do plano passam a ser registros PDF com acesso restrito ao dono/equipe. Essa preservação de comprovantes não é um backup de cobranças ou do banco. Dados dos comprovantes podem identificar a modalidade usada na época; não são opções de pagamento atuais.
- Os testes exclusivos da API removida foram retirados/substituídos. Foram mantidos e adaptados os testes de regras do plano, permissões, preços, créditos, comprovantes, configurações, pagamentos locais e responsividade.

Excluir os registros do site não cancela uma cobrança ainda existente no provedor externo. O site não terá mais como consultar ou receber notificações desses pagamentos. Nenhuma API é chamada para cancelá-los durante a remoção.

## Oracle — depois do merge

**Não executado no ambiente de desenvolvimento:** não há conexão com a Oracle disponível. O procedimento abaixo não cria backup, não exporta cobranças e não copia credenciais.

1. Na pasta efetiva da aplicação, atualizar a branch main pelo procedimento de implantação existente, preservando alterações locais legítimas. Nenhuma chave nova deve ser configurada.
2. Com o código novo instalado, executar como operador autorizado:

```bash
sudo /opt/ponto-coleta/.venv/bin/python /opt/ponto-coleta/scripts/remove_fast_oracle.py \
  --environment-file /etc/ponto-coleta/site.env \
  --service ponto-coleta.service
```

Substituir `/opt/ponto-coleta` pela pasta real. Se a instalação usa `me-guarde.service`, passar esse nome. O script para o site, desabilita e remove as unidades `ponto-coleta-plan-reconcile.timer/service`, retira todas as variáveis `FASTFLOW_*` do arquivo informado, recarrega o systemd e inicia o serviço. Não cria `.bak`, dump, arquivo de cópia ou chave substituta. A gravação do ambiente usa substituição atômica do próprio arquivo, conservando proprietário e permissões.

O endereço público do site, quando configurado anteriormente em `FASTFLOW_PUBLIC_ORIGIN`, é preservado como `SITE_PUBLIC_ORIGIN` para os lembretes, sem manter uma configuração Fast. Gmail, login Google, chave CNPJ do Pix AI e demais variáveis ficam intactos. O timer de lembretes do plano também permanece.

A exclusão dos dados ocorre na primeira inicialização da versão nova. Conferir `systemctl status` e `/health` após a implantação. Em caso de erro, não tratar a operação como concluída: a transação do banco precisa finalizar com sucesso. Outros arquivos de ambiente/drop-ins ou agendamentos cron personalizados, se existirem fora do padrão do repositório, também precisam ter suas referências antigas removidas; o comando cobre o arquivo informado e as unidades conhecidas.

O script não apaga o journal global nem logs gerais de outros serviços, que podem conter evidências de operações preservadas. Não há mais novos logs ou rotinas da integração no código. Não há acesso, exclusão ou cópia de dados de produção nesta entrega.

## Validação

- Assinatura e renovação por Pix AI/dinheiro, confirmação exclusiva do administrador, repetição sem créditos duplicados e início futuro da renovação antecipada.
- Antecipação por cliente/equipe, confirmação de recebimento, abatimento do valor já pago e retirada sem nova cobrança quando o saldo é zero.
- Exclusão dos pedidos antigos em SQLite e PostgreSQL, mantendo ciclos, livro de créditos, valores quitados e comprovantes, com integridade das referências e reinicialização idempotente.
- Ausência de rotas Fast/webhook, credenciais lidas pelo aplicativo, botões/opções e tráfego para a Fast. Os testes impedem chamadas HTTP de cobrança.
- Navegador em 320, 360, 390, 430, 768, 1024 e 1440 px, sem rolagem lateral; contratação, renovação, antecipação e entrega física exercitadas.

Os testes usam apenas dados fictícios e bancos isolados. Não foi criada cobrança real, nova chave ou backup do GitHub/Oracle.
