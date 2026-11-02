# Área do Cliente: ações por assunto e aceite organizado

## O que mudou

- O cartão de acesso à central conserva seu botão laranja, contador e explicação. O link azul-turquesa repetido “Resolver pendência” foi removido. As solicitações continuam com sua ação principal laranja.
- A central apresenta **Dados cadastrais**, **Contrato** e outras solicitações com títulos, instruções e estados próprios. O contador considera cada assunto cadastral/contratual pendente separadamente.
- A tela de aceite não contém o texto “Se faltam dados cadastrais...” nem o botão “Conferir dados e regularização”. Se os dados do titular estiverem incompletos, mostra a dependência concreta e direciona à solicitação cadastral pendente correspondente, ou à regularização cadastral existente. O formulário de assinatura não é exibido nessa situação; o servidor também impede envio de código e aceite desse contrato até completar os dados necessários.
- Novas solicitações não podem combinar `profile` com aceite, assinatura, confirmação por e-mail ou outros assuntos. Solicitações publicadas e seus textos/históricos não são reescritos por esta atualização. Para reorganizar uma solicitação antiga que tenha sido redigida com assuntos misturados, o administrador pode cancelar a versão aberta e publicar solicitações independentes; as respostas antigas permanecem preservadas.

A regularização existente foi reaproveitada em duas rotas autenticadas:

- `/cliente/pendencias/dados`: dados cadastrais, sem assinatura ou confirmação contratual. Depois de salvar, mostra somente a conclusão cadastral e acesso à central.
- `/cliente/pendencias/contrato`: aceite/assinatura ou confirmação do contrato, sem formulário cadastral. Uma dependência necessária aponta para a tela cadastral.
- `/cliente/pendencias` continua como entrada compatível: escolhe um único assunto pendente por página. Salvar dados não muda essa tela para um aceite automaticamente.

Cada confirmação grava somente sua própria resposta. Salvar o contrato não responde a uma autorização opcional ou a uma solicitação independente de revisão cadastral. Requisitos equivalentes de uma mesma revisão continuam reaproveitando as evidências válidas já existentes, sem criar duplicidade.

## Ordem do aceite

Após o texto contratual e as instruções da revisão:

1. **Assine abaixo**, quadro para dedo/mouse e **Limpar assinatura**.
2. Checkbox desmarcado **“Li e aceito os termos apresentados nesta versão.”**, à esquerda do texto. O fundo fica verde com check branco quando marcado; clicar no texto também alterna a seleção. O texto quebra linha sem deslocar o controle.
3. **Enviar código por e-mail**. O envio em segundo plano não submete o aceite nem armazena a assinatura desenhada. A assinatura e a seleção permanecem na página ao enviar ou reenviar.
4. Mensagem verde somente após o provedor aceitar o envio: **“Código enviado com sucesso para o e-mail di***@gmail.com.”**. O endereço é derivado exclusivamente do cliente autenticado, com os dois primeiros caracteres do nome local e o domínio; `di` é apenas exemplo. Falha de envio, limite, conexão ou sessão expirada não produz mensagem de sucesso.
5. **Código recebido**, campo que permite colar e sugere preenchimento automático, quando o navegador disponibiliza.
6. **SALVAR DADOS**, laranja. Enter no campo de código salva em vez de reenviar o código.

Quando a regularização exige apenas confirmar por e-mail um aceite histórico válido, a assinatura já existente permanece preservada e exibida; não se solicita ou fabrica outra assinatura desnecessariamente.

## Validação e persistência

O servidor continua validando cliente ativo/sessão, CSRF, solicitação, revisão/texto, aceite expresso, imagem de assinatura e desafio de e-mail com escopo, validade, limites de tentativas e de reenvio. E-mails ou identificadores enviados pelo navegador não substituem o titular/destinatário autenticado. O envio do código não é autorização.

Ao salvar com todas as evidências, o contrato e a resposta são gravados na mesma transação. Somente depois do commit há redirecionamento para a central e a mensagem **“Dados atualizados com sucesso.”**. Erro de validação não conclui a solicitação. Erro de gravação faz rollback e apresenta erro, sem sucesso e sem aceite parcial. A tela conserva a assinatura válida e o checkbox nos erros de formulário recuperáveis.

Os dados contratuais originais, assinaturas, datas e aceites anteriores continuam preservados. Retirada, pagamentos e comprovantes de pacotes já recebidos não foram alterados. Nenhuma credencial Google/Gmail, configuração Oracle ou estrutura de dados foi modificada nesta atualização.

## Verificação

Resultado: **220 testes aprovados em SQLite e oito em PostgreSQL isolado (228 ao todo)**. Os 25 testes focados em aceite/regularização passaram novamente após o ajuste final. A regressão da central passou nas sete larguras e a regularização existente passou em 24 cenários de navegador, incluindo Google e cadastros antigos.

- Pytest: ausência de botões repetidos, ordem do formulário, checkbox inicialmente desmarcado, escopo do código, envio somente ao e-mail autenticado, máscara, sucesso/falha do provedor, dependência de dados, rotas separadas, contador por assunto, rejeição de novas solicitações combinadas, assinatura/checkbox/código obrigatórios, código incorreto/expirado, resposta somente à solicitação atual, preservação literal do cadastro e rollback com falha simulada na gravação.
- PostgreSQL isolado: fluxo contratual/cadastral, códigos, gravação e histórico.
- Chromium em **320, 360, 390, 430, 768, 1024 e 1440 px**: assinatura por toque/mouse, seleção pelo texto, verde/check branco, ordem, alinhamento, envio e reenvio, provedor indisponível, máscara real, assinatura/checkbox mantidos, orientação/redimensionamento, colar/preencher código, erros, checkbox obrigatório, conclusão individual e histórico preservado. Sem rolagem horizontal da página.
- Provedor de e-mail e dados são fictícios. Não houve envio real, acesso à Oracle ou alteração de credenciais. Navegador emulado não substitui a conferência final em aparelhos físicos.

Relatório e capturas temporários: `/tmp/adelinha-acceptance-results`. A implantação exige merge e atualização/reinício do serviço existente; esta tarefa não reinicia a Oracle.
