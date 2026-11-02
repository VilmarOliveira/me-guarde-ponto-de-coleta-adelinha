# Central de pendências e autorizações

## Publicar uma solicitação

1. Entre como **administrador** e abra **Área do Ponto → Configurações → Pendências e autorizações** (`/painel/configuracoes/pendencias`). Ter apenas a permissão de configurações, como gerente, não permite administrar a central.
2. Preencha título, explicação (o que mudou e por quê) e texto completo. Escolha **aviso informativo**, **regularização obrigatória** ou **autorização opcional**.
3. Selecione o público: todos (incluindo novos clientes), IDs públicos específicos ou clientes com requisito pendente. A seleção por requisito usa as mesmas verificações do cadastro/contrato existente.
4. Escolha as ações e o início, no horário de Brasília. O prazo é opcional; seu vencimento não cria aceite, não conclui a solicitação e não impede a regularização posterior.
5. Para uma regularização obrigatória, selecione as funções dependentes. Nesta implementação: **Dados para novas entregas** e **Registrar novas encomendas**. A primeira restringe o cartão/endpoint de orientação para novas entregas; a segunda impede novos registros de encomendas pela equipe, inclusive POST direto. Se nenhuma for selecionada, a pendência não bloqueia funções. Endereço comercial público não é tratado como informação sigilosa.
6. **Salvar rascunho e visualizar prévia** não publica. Confira texto, público, ações, datas e restrições, marque a confirmação e clique **Publicar solicitação**. Não publique dados de outros clientes no texto de uma solicitação geral.
7. Use **Acompanhar** para consultar estados e evidências, com filtro por ID do cliente. O administrador não possui ação para responder pelo cliente.

Solicitações futuras só aparecem e restringem após o início. Clientes existentes elegíveis são vinculados na publicação quando já iniciada; novos clientes e critérios agendados são avaliados ao acessar a central/conta ou o acompanhamento. Não existe tarefa externa ou e-mail de convocação em massa. As notificações de pendência são exibidas no sistema; e-mails são enviados a pedido do próprio cliente para confirmar uma ação.

## Como o cliente resolve

Após entrar por senha ou Google, o cliente vê o contador e o aviso na Área do Cliente. Em **Pendências e autorizações**, abre **Resolver pendência**, lê o texto da versão e executa as ações solicitadas:

- **Leitura/termos:** confirmação expressa pelo formulário; visualizar ou entrar não responde.
- **Dados:** usa a edição/regularização já existente e volta para concluir. O servidor exige um salvamento pelo próprio cliente após a atribuição, com os campos obrigatórios válidos.
- **Assinatura:** desenha no campo existente; redimensionar ou solicitar código não apaga o desenho.
- **E-mail:** solicita o código para seu endereço cadastrado e o informa ao concluir. O desafio é vinculado ao cliente, e-mail e solicitação/versão; outro fluxo pode substituir o desafio anterior. Expira em dez minutos, com cinco tentativas e limites de reenvio. Nenhum código é guardado em texto puro nas tabelas dessa confirmação.
- **Opcional:** pode **Aceitar** ou **Recusar**. Recusar dispensa assinatura e código, não bloqueia a conta e fica no histórico. Autorizações aceitas e ainda publicadas podem ser revogadas pela própria central, preservando ambas as respostas.

Clientes ativos com pendências podem autenticar pelo Google e acessar a regularização. O primeiro vínculo ainda exige a senha do cadastro existente. Contas inativas continuam bloqueadas, não há cadastro automático nem permissão da equipe. O acesso a dados para novas entregas permanece restrito enquanto o cadastro autenticado pelo Google não estiver regularizado. O login por senha existente é preservado.

**Retirada e pagamento de pacotes já recebidos, comprovantes, contratos anteriores, suporte, saída e regularização nunca são bloqueados por estas solicitações.** O servidor valida as funções dependentes em cada operação; ocultar botões não é a proteção.

## Contratos, versões e cancelamento

Para aplicar a exigência a um contrato **já vigente**, use a nova seção **Contrato vigente** desta tela. O procedimento, a identificação imutável de revisões e o comando de implantação estão em [Correção do aceite de revisão](correcao-aceite-revisao-contrato.md).

O formulário permite **Publicar este texto como contrato vigente para novos cadastros**, com código técnico exclusivo e título. Não preencha outro texto com um código já utilizado: o servidor verifica os contratos históricos e as versões publicadas.

- Sem **Exigir novo aceite dos clientes existentes**, a publicação é informativa para eles; novos cadastros usam o novo texto na data de início. Não modifica aceites antigos.
- Com a opção marcada, o público é quem ainda não tem aceite válido daquele texto/versão. São exigidos novo aceite, assinatura e código de e-mail. Quem já tem evidência válida não precisa aceitar outra vez. Pendências cadastrais/contratuais equivalentes são agrupadas na central; a regularização anterior continua disponível.
- `/condicoes`, seu PDF, o formulário e o código do cadastro usam a versão vigente. Um formulário aberto antes da mudança é rejeitado como desatualizado; não recebe um contrato diferente silenciosamente.
- Novos aceites são registros adicionais. Os campos contratuais originais do cliente, imagens de assinatura e confirmações antigas não são sobrescritos. A conta mostra o registro efetivo e permite consultar os anteriores.
- Conteúdo publicado é imutável. **Criar nova versão como rascunho** preserva a anterior. Um clone sem mudança de conteúdo/configuração não publica nova cobrança de ação. Publicar uma versão substitui as pendências abertas da mesma solicitação; a anterior fica cancelada e suas respostas são preservadas. Se o início da substituta for futuro, haverá um intervalo sem exigência dessa solicitação: escolha as datas conscientemente.
- **Cancelar solicitação publicada** encerra as pendências ainda abertas e preserva as respostas anteriores. Cancelar a solicitação de novo aceite não desfaz um contrato já publicado ou aceito; para alterar o contrato vigente, publique uma nova versão explicitamente.

Mudanças de código, layout e textos informativos não publicam solicitações automaticamente. O sistema não inicia nenhuma exigência nem altera contratos ao instalar esta atualização.

## Persistência e implantação

As tabelas `client_action_requests`, `client_action_assignments`, `client_action_responses` e `client_published_contract_versions` são criadas de forma aditiva na inicialização, em SQLite e PostgreSQL. Os históricos contratuais e a tabela compartilhada de desafios de e-mail são reaproveitados. As respostas registram texto/versão/hash, cliente, ação/escolha, data/hora, IP/navegador e as evidências exigidas. Clientes com vínculos na central são desativados em vez de removidos fisicamente.

Não há novas dependências ou variáveis. Preserve `/etc/ponto-coleta/site.env`, as credenciais Google/Gmail, o banco persistente e o procedimento de backup já utilizado. Após merge, atualize o checkout limpo, execute a suíte com dados temporários e reinicie o serviço existente conforme o guia Oracle. A inicialização aplica as tabelas novas. Não execute testes contra produção e não substitua `DATABASE_URL`/`DATABASE` por banco vazio.

Esta tarefa não acessa, reinicia ou altera a instalação Oracle.

## Verificação

Resultado final: **209 testes pytest aprovados**, incluindo os sete de PostgreSQL. Os testes de navegador da central passaram nas sete larguras; as suítes existentes de Google e regularização também passaram, com 24 cenários cada.

- Pytest: ambos os logins, conta inativa, requisito real, público específico, agendamento, rascunho/prévia, CSRF, permissão administrativa, acesso direto, não duplicidade, recusa/revogação, e-mail indisponível/expirado/escopo incorreto, assinatura, cancelamento, contrato vigente, histórico e reinicialização.
- A retirada de um pacote anterior é concluída enquanto o registro de novas encomendas está bloqueado; o comprovante permanece acessível.
- PostgreSQL 16 isolado: schema, publicação, desafio, assinatura/resposta, versões e persistência após reinicialização.
- Chromium: administração e cliente em **320, 360, 390, 430, 768, 1024 e 1440 px**, com senha, Google simulado, assinatura por toque/mouse, redimensionamento, solicitação de código sem perder assinatura, recusa, restrição, nova versão e cancelamento. Sem rolagem horizontal da página. Relatório temporário em `/tmp/adelinha-actions-results`.

Os testes usam clientes fictícios, provedor Google simulado e e-mails interceptados; não enviam mensagens reais. Token Google com assinatura RSA é validado separadamente pela suíte existente. Não substituem uma conferência final na instalação Oracle com a configuração real de e-mail nem testes físicos em iOS/Android.
