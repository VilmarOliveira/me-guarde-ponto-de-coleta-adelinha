# Exigir aceite de uma revisão contratual já vigente

## Diagnóstico no código

A central já comparava `terms_version` e `terms_text` literalmente em `accepted_contract()`. O rótulo visual “Contrato — Versão 1” não era o critério de comparação. Contudo, essa verificação só gerava uma pendência quando havia uma solicitação contratual **publicada com `require_existing=1`**. A validação cadastral (`compliance()`) atesta as evidências do contrato que a pessoa assinou, sem impor por conta própria o contrato atual. Assim, publicar o texto sem exigir novo aceite, ou atualizar o contrato do código sem publicar uma solicitação explícita, não convocava os clientes antigos.

Também foi encontrado um erro independente: confirmar a leitura de um aviso de contrato, mesmo sem exigência de novo aceite, podia acrescentar um registro contratual incompleto, sem assinatura, com metadados de confirmação indevidos. A leitura agora registra somente a resposta informativa, sem assinatura, confirmação por e-mail ou contrato novo. Registros antigos associados comprovadamente a esses avisos continuam preservados, mas são excluídos da escolha do contrato assinado efetivo e da comprovação do novo aceite. Não se presume que esses registros existam em produção: o banco Oracle não foi acessado nesta tarefa.

## Identificação e evidências

- O título visual continua personalizável, inclusive “Contrato — Versão 1”. Não renumeramos títulos ou versões anteriores.
- O código técnico continua exclusivo para um texto, com rejeição de reutilização para texto diferente.
- `REV-<SHA-256>` identifica a combinação exata de código técnico e texto. Não depende do título, do cliente ou da data. Não é o `contract_id`, que identifica cada aceite individual.
- Novos cadastros e aceites guardam `contract_revision_id`, além dos campos já existentes. A coluna é adicionada sem preencher ou reescrever os registros anteriores. Para um aceite antigo sem esse campo, a revisão é derivada somente para comparação a partir do código e do texto originais, exigindo assinatura válida e confirmação de e-mail reais.
- O identificador aparece na revisão apresentada e nos novos documentos. PDFs históricos sem o novo campo não ganham metadados retroativos.
- Uma resposta marcada “aceita” isoladamente não conclui a pendência: deve existir evidência válida daquele texto/código/revisão. Não se copia a assinatura de outro contrato. Uma confirmação cadastral antiga não confirma uma revisão diferente.

## Aplicar à atualização já publicada pelo painel

Após o merge e a implantação:

1. Como administrador, abra **Área do Ponto → Configurações → Pendências e autorizações**.
2. Em **Contrato vigente**, confira título, código, identificador, texto integral e a quantidade de clientes ativos que ainda não têm evidências válidas dessa revisão. O contador não revela dados de outras contas aos clientes.
3. Informe o **resumo real das mudanças e motivo do novo aceite**, marque **Exigir novo aceite dos clientes existentes** e clique **Preparar exigência do contrato vigente**.
4. Confira a prévia e publique. Se desejar, volte ao rascunho para ajustar prazo ou funções dependentes. Por padrão, essa correção não bloqueia funções: apenas exige a ação na central.
5. Clientes que só assinaram a revisão antiga recebem a pendência. Quem já aceitou validamente o mesmo código e texto não precisa repetir. Repetir a preparação/publicação não duplica solicitações ou respostas.

Isso funciona para o contrato padrão já entregue pelo código e para contratos já publicados pelo painel. Neste último caso é criada uma nova versão **da solicitação**, mantendo o mesmo documento contratual; o aviso anterior e suas respostas não são convertidos em aceites. O resumo original continua preservado. Novas alterações contratuais usam o formulário normal e a mesma opção de exigir aceite.

O reinício ou uma alteração de layout não publica exigências automaticamente. A operação explícita acima aplica a regra ao texto vigente sem mudar o contrato antigo nem criar um novo deploy só para solicitar o aceite.

## Alternativa para o operador da Oracle

Use o banco persistente e as variáveis já existentes. Não altere as credenciais Google/Gmail nem `/etc/ponto-coleta/site.env`. Execute na pasta do projeto, com o ambiente do serviço carregado e depois de a nova aplicação inicializar as colunas/tabelas aditivas. Faça o backup habitual antes de aplicar a atualização.

O comando sem opções de escrita é apenas um relatório: não imprime nomes, CPF, e-mails, assinaturas, códigos de confirmação ou credenciais.

```bash
.venv/bin/python scripts/require_current_contract_acceptance.py
```

Copie o identificador `REV-...` do relatório e prepare o rascunho, com o resumo real:

```bash
.venv/bin/python scripts/require_current_contract_acceptance.py \
  --prepare --expected-revision 'REV-IDENTIFICADOR_DO_RELATORIO' \
  --summary 'RESUMO REAL DAS MUDANCAS E DO MOTIVO DO NOVO ACEITE'
```

Abra a URL de prévia informada, autenticado como administrador. Publique pela tela ou, após revisar, use o hash exato dessa prévia:

```bash
.venv/bin/python scripts/require_current_contract_acceptance.py \
  --publish --expected-revision 'REV-IDENTIFICADOR_CONFERIDO' \
  --preview-hash 'HASH_EXATO_DA_PREVIA_REVISADA' \
  --summary 'RESUMO REAL DAS MUDANCAS E DO MOTIVO DO NOVO ACEITE'
```

Revisão divergente ou hash de prévia desatualizado impede a publicação. Preparar não envia e-mails e não registra aceites em nome dos clientes. Não informe uma URL de banco com senha na linha de comando; mantenha a configuração no ambiente existente.

## Fluxo do cliente e limites

A pendência aparece após entrar por senha ou Google e na central. O cliente lê o resumo e o texto da revisão, assina, solicita o código ao e-mail cadastrado e confirma expressamente o aceite com esse código. Apenas então são gravados o novo contrato e a resposta. A pendência deixa de exigir ação e fica consultável no histórico; o contrato antigo, assinatura e data continuam disponíveis.

A retirada, o pagamento e os comprovantes de pacotes já recebidos permanecem disponíveis. Nenhuma condição nova altera valores ou registros desses pacotes. Eventuais funções dependentes continuam restritas à lista permitida na central e são verificadas no servidor.

## Verificação

A suíte completa passou com **220 testes**, incluindo oito em PostgreSQL isolado. Após o último ajuste do relatório somente leitura, os **11 testes específicos da correção** passaram novamente. A extração de texto dos PDFs confirmou a revisão idêntica no documento vigente e no novo assinado, sem acrescentá-la a um documento histórico sem esse campo.

Os testes usam clientes fictícios e e-mails/Google simulados. Cobrem revisão antiga e vigente com o mesmo título visual, código/texto diferentes, texto diferente sob o mesmo código, compatibilidade com evidências antigas sem novo identificador, ausência de assinatura/código, status de resposta sem evidências, leitura informativa, preservação de avisos antigos, publicação explícita do contrato já vigente, repetição sem duplicidade, relatório/implantação por CLI e migração sem reescrever evidências.

A regressão da central também passou nas sete larguras de 320 a 1440 px. O Chromium verifica senha e Google, publicação já realizada sem exigência, ativação posterior, desenho por toque/mouse, confirmação por e-mail, desaparecimento da pendência e permanência do histórico em 320, 390, 768 e 1440 px. Limitação: testes em navegador emulado, sem acessar a Oracle ou enviar mensagens reais.
