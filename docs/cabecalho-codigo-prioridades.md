# Cabeçalho, código por e-mail e pagamentos

## Cabeçalho

Para visitantes, computador: Dúvidas, Blog, Cadastre-se, Login Cliente, Área do Ponto. Cadastro e login usam laranja com texto branco. No celular, a marca fica acima da linha Cadastre-se / LOGIN / Menu para os três textos caberem em 320 px. O menu abre Dúvidas, Blog, Área do Cliente e Área do Ponto em linhas individuais. Cadastro continua oculto em sessões autenticadas; navegação interna e permissões permanecem separadas.

## Diagnóstico do código

O fluxo real primeiro verifica a revisão do contrato, os limites de reenvio, o CPF e o e-mail; somente depois chama `send_code` → renovação OAuth Gmail → `messages.send`. CPF formatado é normalizado. As validações foram mantidas: o diagnóstico agora identifica CPF ou e-mail inválido e foca o campo, em vez de deixar uma mensagem genérica. No navegador, valida somente os campos necessários ao pedido de código, evita cliques concorrentes e preserva os campos, o aceite e a assinatura. O sucesso abaixo do botão só aparece após resposta bem-sucedida do servidor, que exige aceitação pela Gmail API com identificador de mensagem. A mensagem contém e-mail real parcialmente mascarado, inclusive domínio.

Neste ambiente não há credenciais Gmail configuradas (`email=False`). Não há acesso à Oracle ou às respostas da instalação em produção. Portanto, **não foi possível estabelecer a causa operacional do não recebimento relatado** nem afirmar que as credenciais da Oracle estão inválidas. O problema confirmado no código era a falta de distinção no formulário entre bloqueio por CPF/e-mail e falha do provedor. Foram simulados envio aceito, ausência de confirmação da API e HTTP 403, além dos dados inválidos que não chegam à API.

Na Oracle, conferir em Configurações → Notificações o estado do e-mail; verificar se o serviço carrega `/etc/ponto-coleta/site.env`. Preservar `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` e `GMAIL_SENDER=pontodecoletaadelinha@gmail.com`. As variáveis `GOOGLE_LOGIN_*` têm finalidade diferente e não devem substituí-las. A tela mostra a recusa do Gmail/OAuth, quando houver, sem declarar envio concluído. O diagnóstico de falha registra apenas `registration_code_failed stage=gmail configuration_ready=...`, sem código, destinatário, senha ou token. Uma aceitação pela API não garante entrega na caixa de entrada; verificar spam e a conta remetente.

## Pix e prioridade

O PR #64 já foi mesclado e seu fluxo foi preservado: Pix não funcionou consulta a Fast, valida o vínculo e o valor, solicita DELETE somente para cobrança pendente e cria outra tentativa Pix Fast apenas depois do encerramento sem pagamento confirmado. Primeira assinatura e renovação foram verificadas com API simulada, incluindo repetição do clique. Valor, finalidade comercial e histórico permanecem preservados.

HTTP 403 indica recusa da Fast; não comprova pagamento, cancelamento ou expiração. A mensagem agora explica a recusa e mantém a cobrança anterior, sem gerar outro Pix ou mudar para Pix AI automaticamente. A transação real 127471 não foi consultada neste ambiente: o 403 foi reproduzido por simulação. A equipe deve verificar as permissões/credencial da integração e a vinculação da transação com a Fast.

A seleção existente de prioridades foi validada por salvamento no formulário administrativo nas três abas independentes. Sem cobrança em aberto, Pix AI em primeiro, ativo, configurado e elegível é usado no antecipado, assinatura ou renovação correspondente. Com cobrança já emitida, conserva-se o snapshot de modalidade/valor; agora há um aviso explicando por que a nova ordem não se aplica. Não há alteração de mensalidade, preço de pacote ou limites da integração.

## Verificação e implantação

`tests/test_header_mail_priority.py` e os testes já existentes usam API/e-mail simulados, sem cobranças ou envios reais. `tests/browser/header_mail_priority.py` verifica 320, 360, 390, 430, 768, 1024 e 1440 px, ordem e cores do cabeçalho, menu, mensagem de código, falha sem falso sucesso, campos preservados e prioridade Pix AI.

Após merge, seguir o procedimento habitual de backup, `git pull --ff-only origin main` e reiniciar o serviço efetivo da instalação. Sem migração de banco, dependências, credenciais ou agendamentos novos. A implantação Oracle e o envio real continuam dependendo de conferência na instalação. Inclui também o ajuste já solicitado dos rótulos do endereço residencial, que ainda não constava na main.
