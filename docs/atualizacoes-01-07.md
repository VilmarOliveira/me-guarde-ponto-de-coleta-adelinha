# Atualizações pendentes 01, 02, 03, 04, 05 e 07

- 01: ícones de encomenda com fita laranja e etiqueta; o grande é maior que o pequeno.
- 02: ícone de entrada com usuário, cadeado e seta, inclusive no cabeçalho mobile.
- 03: normalização das credenciais Gmail e diagnóstico de OAuth em Configurações → Envio de códigos → Testar autorização do Gmail. O teste não envia e-mail nem exibe tokens. Mensagens identificam os campos relacionados a invalid_client e invalid_grant. O código não consegue substituir credenciais reais inválidas: ID, segredo e refresh token precisam pertencer à mesma autorização no arquivo /etc/ponto-coleta/site.env, seguido do reinício do serviço. Autorização válida não confirma entrega do e-mail; concluir o teste por Esqueci minha senha.
- 04: uma única seta nos botões Voltar/Anterior; a decoração pode ser reaplicada sem duplicar ícones e sem alterar os formulários.
- 05: pacote fechado com relógio para aguardando; pacote aberto com confirmação para retirados.
- 07: um Pix Fast para o saldo de vários pacotes selecionados. Contratação e renovação de planos continuam com o fluxo já corrigido na atualização 06.

## Pix agrupado

O cliente seleciona os pacotes e confere o saldo antes de confirmar. O servidor consulta as cobranças anteriores, desconta pagamentos já confirmados e recalcula o total sob bloqueio dos registros. Cobranças iguais são reutilizadas. Quando a seleção inclui outros pacotes, o formulário oferece um Pix único. É necessário incluir todos os pacotes de qualquer cobrança anterior envolvida. Cobranças pendentes com acréscimo ou desconto manual exigem conferência da equipe, para não perder o ajuste ao agrupar.

A nova cobrança e a transferência das reservas são registradas atomicamente. As anteriores ficam marcadas como substituídas, conservando QR, valor, transação e histórico; seus links apontam para a cobrança atual. O código anterior não é cancelado na Fast e ainda pode ser pago por quem já o copiou.

Falhas de consulta, divergências ou pagamento em análise impedem substituição. A modalidade continua Pix Fast e respeita habilitação e limites atuais. Uma falha conhecida antes da criação remota restaura as reservas e a exibição do Pix anterior. Uma criação incerta mantém a nova reserva e aguarda a consulta, sem repetir o POST.

Pagamentos recebidos em uma cobrança substituída ficam para conferência. O QR da cobrança atual é ocultado, toda a cadeia é sinalizada e o sistema não gera quitação automática adicional nem permite retirada com valores em análise. O código não executa devoluções na Fast; a equipe deve conferir o pagamento e o tratamento do valor antes de liberar o caso.

O limite existente de seleção continua em 150 pacotes. Consultas respeitam o intervalo existente do provedor. Não há atualização destrutiva dos pagamentos ou dados reais de clientes.

## Verificação

Testes simulados cobrem seleção com dois ou mais pacotes, múltiplos Pix anteriores, dedução de pagamento, criação repetida, falha e incerteza na API, erro no banco, alteração do saldo, pagamento tardio, consulta dos predecessores e concorrência no PostgreSQL em esquema isolado. A recuperação de senha é exercitada com OAuth e Gmail simulados. A validação dos elementos verifica ícones únicos e preservação dos formulários; não substitui inspeção visual em navegador real.

A inspeção de colunas PostgreSQL identifica a tabela visível pela conexão, inclusive em esquema isolado; a migração não presume que todas as tabelas estejam em public. No CI, os testes transacionais do agrupamento rodam primeiro e o restante da suíte roda depois, sem repetir o mesmo arquivo.
