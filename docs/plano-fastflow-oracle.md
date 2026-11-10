# Nova configuração Pix Fast na Oracle

A integração volta com novas variáveis de ambiente. O código não importa
credenciais, registros, backups ou cobranças antigas. Confirme que a remoção
anterior foi aplicada no servidor antes de habilitar novas cobranças. A
reinstalação não cancela cobranças que ainda existam na conta externa. Pix AI,
dinheiro, créditos e comprovantes anteriores continuam disponíveis.

## Ativar em duas etapas

1. Instale a versão mesclada na Oracle. Identifique o arquivo de ambiente real:
   `sudo systemctl show ponto-coleta.service -p WorkingDirectory -p EnvironmentFiles -p User`.
2. No painel da Fast, gere uma chave da integração do tipo FastFlow e configure
   o webhook HTTPS em `/webhooks/fastflow` sob o domínio público atual do site.
   Selecione os eventos `transaction.paid`, `transaction.expired` e
   `transaction.refunded`. Use o segredo de assinatura associado às cobranças
   dessa conta; valide a configuração pelo teste do painel.
3. Edite `sudoedit /etc/ponto-coleta/site.env`, se for o arquivo do serviço.
   Preserve as outras variáveis. Preencha os valores diretamente no servidor;
   não copie os exemplos literalmente nem envie as credenciais ao chat/Git.

```dotenv
FAST_PIX_ENABLED=1
FAST_PIX_NEW_CHARGES_ENABLED=0
FAST_PIX_API_KEY=<chave-nova>
FAST_PIX_WEBHOOK_SECRET=<segredo-confirmado-na-Fast>
FAST_PIX_PUBLIC_ORIGIN=<origem-https-publica-sem-barra-final>
```

4. Reinicie `sudo systemctl restart ponto-coleta.service`. Em **Área do Ponto →
   Configurações → Plano ME GUARDE+**, use **Verificar chave e provedor**.
   Execute o teste de webhook no painel da Fast. Ambos devem passar antes de
   habilitar cobranças. O webhook usa HMAC e a API é consultada novamente para
   validar estado, pedido e valor. Um teste de webhook não comprova validade
   bancária do QR Code.
5. Só depois, mude `FAST_PIX_NEW_CHARGES_ENABLED` para `1` e reinicie.
   Em **Pagamentos → Ordem e limites**, posicione o Pix Fast conforme a
   preferência de cada tela. A ordem de Pix AI e Dinheiro anterior à
   reativação é preservada. Confira uma nova cobrança de teste no painel
   da Fast e no aplicativo do banco, sem concluir o pagamento se o objetivo
   for testar apenas o QR Code.
6. Para reconciliação automática, instale as unidades em
   `deployment/systemd/ponto-coleta-plan-reconcile.*`, ajustando o usuário,
   pasta e Python ao serviço real antes de ativar o timer. A rotina consulta
   e atualiza cobranças; não gera novos pagamentos.

Os nomes antigos `FASTFLOW_*` não são carregados do ambiente. Cartão permanece
desativado. Para pausar apenas novas cobranças, use
`FAST_PIX_NEW_CHARGES_ENABLED=0`; consultas e webhooks continuam com
`FAST_PIX_ENABLED=1`.
