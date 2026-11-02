# Regularização do cadastro, Google e nova série do contrato

## Diagnóstico do bloqueio

O `eligibility()` anterior exigia presença de `contract_id`, `contract_hash`, metadados da confirmação por e-mail e assinatura começando exatamente com `data:image/png;base64,`. A Área do Cliente não aplicava nem exibia esse diagnóstico. Assim, o login por senha funcionava mesmo quando o Google recusava o cadastro, sem orientar o cliente.

A base local disponível está vazia; a conta relatada e o banco Oracle não foram acessados. Não é possível afirmar qual coluna dessa conta estava vazia apenas pela mensagem genérica. Para identificar exatamente no servidor, a auditoria abaixo mostra os requisitos que a validação anterior recusaria e as pendências atuais, sem imprimir nomes, e-mails, CPF, texto contratual ou assinaturas.

Agora o mesmo diagnóstico é usado no Google e na Área do Cliente. Identificador/hash de controle ausentes, isoladamente, não invalidam um aceite histórico que tenha versão, texto, data, identificação do titular e assinatura reais. Não são gerados valores substitutos para esses campos. Assinaturas PNG, JPEG e WebP e base64 puro são reconhecidos **somente após decodificar e validar o arquivo real**. Arquivos ilegíveis, dados arbitrários e SVG não são tratados como assinatura válida; o original permanece preservado. A normalização é apenas para leitura/exibição, sem regravar a evidência.

A confirmação por e-mail continua obrigatória para regularizar as evidências contratuais. Desde a central de pendências, sua ausência não impede autenticar um cliente ativo pelo Google; ele pode regularizar no sistema, com as funções dependentes restritas. Evidências válidas: evidência histórica com canal `email`, destinatário válido e data válida, ou confirmação adicional efetivamente realizada pelo cliente, vinculada ao fingerprint do aceite. A indicação técnica de que um e-mail foi enviado não é confundida com confirmação. Dados de contato/endereço são validados, e conta inativa continua bloqueada.

## Fluxo do cliente

1. Entre com CPF ou e-mail e senha. A Área do Cliente apresenta a lista dos itens que faltam e o botão **Regularizar meu cadastro**.
2. Em `/cliente/pendencias`, complete os dados faltantes. Nome e CPF já válidos ficam bloqueados nessa tela; valores inválidos podem ser corrigidos, com validação de CPF e duplicidade. O ID e os pacotes não mudam.
3. Se o aceite histórico estiver completo e faltar apenas a confirmação por e-mail, leia o contrato aceito e solicite o código. Confirme o aceite e digite o código recebido. Isso acrescenta uma prova de confirmação; não cria nem copia uma assinatura para simular um novo aceite.
4. Se faltarem assinatura, identificação do titular, texto, versão ou data do aceite, leia o contrato da nova série, desenhe sua assinatura, solicite o código e confirme o aceite. É criado **um novo registro**, mantendo o anterior intacto.
5. Depois de regularizar, saia e escolha **Continuar com o Google**. O primeiro vínculo continua exigindo a senha do cadastro existente; vínculos já registrados continuam usando o `sub` do Google. Não há criação automática de conta.

Os detalhes das pendências só aparecem após autenticação do cliente. O retorno Google autentica clientes ativos, inclusive com pendências, e permite consultar a própria central de regularização. O primeiro vínculo mantém a confirmação da senha existente; contas inativas não entram. IDs em parâmetros de consulta não selecionam outra conta. O histórico e os PDFs verificam o proprietário no servidor.

O envio do código usa a Gmail API já configurada. Nenhuma credencial Google/Gmail foi alterada. Os códigos ficam em tabela própria no servidor, expiram em 10 minutos, têm limite de cinco tentativas e de três envios em 15 minutos, com intervalo de 60 segundos. Ficam vinculados ao cliente, e-mail, dados identificadores e ao texto/versão apresentados. Alterar esses dados invalida o uso do código anterior. Falha de envio não confirma nada. Solicitar/reenviar um código não apaga a assinatura que está sendo desenhada.

## Preservação do histórico

- `clients` mantém os campos originais de assinatura e aceite. Regularização contratual não os sobrescreve.
- `client_contract_records` preserva uma cópia literal dos dados anteriores antes de completar dados identificadores ou acrescentar novo aceite. Novos aceites são registros separados, com assinatura efetivamente apresentada, código confirmado, data, IP, navegador, texto, versão e hash próprios.
- `client_contract_email_confirmations` acrescenta confirmação por e-mail a um aceite específico, sem alterar assinatura, texto ou data do aceite original.
- `/cliente/contratos` apresenta os registros e confirmações; cada PDF histórico é limitado ao próprio cliente. O contrato corrente do cliente usa o último novo aceite, quando houver, e o anterior continua disponível no histórico.
- Pacotes, retiradas, valores e comprovantes de pagamento não são alterados. A desativação de cliente com histórico também considera os novos registros de aceite.

## “Contrato — Versão 1” e o conflito encontrado

O histórico do código mostra que a página antiga exibia `Versão v1` para um contrato cujo identificador era `CONTRATO-2026-v2`; esse texto difere do vigente anterior `CONTRATO-2026-v3`. Após informar o conflito, o proprietário autorizou **criar uma nova série com Versão 1 e código técnico exclusivo**.

A nova identificação é:

- Título: **Contrato — Versão 1**.
- Código técnico: **ADELINHA-SERIE-2026-11-v1**.
- Texto: mantém o conteúdo vigente, incluindo a cláusula de indenização; não substitui os textos aceitos de v1/v2/v3 anteriores.

O título e o código aparecem no contrato online, no PDF vigente, no comprovante de novo cadastro e nos novos aceites/PDFs assinados. O título é salvo junto ao novo aceite, e não calculado retroativamente para os antigos. `contract_v3.py` arquiva a versão anterior, além do arquivo v2 já existente. Nenhum registro existente é renumerado nem ganha o novo título automaticamente. Cada novo aceite guarda o texto e a versão apresentados, validados por hash e código de e-mail.

## Auditoria somente leitura

Na pasta do aplicativo, com o ambiente de banco usado pelo serviço disponível:

```bash
.venv/bin/python scripts/audit_client_compliance.py
.venv/bin/python scripts/audit_client_compliance.py --client-id 123
```

`123` é o ID interno do cliente, não o número completo do código público. O programa usa `DATABASE_URL`/`DATABASE`, não recebe conexões secretas na linha de comando, não chama migrações e ativa transação somente leitura. Compara versões e hashes de texto e lista contagens de requisitos recusados. `previous_google_gate_failures` identifica o motivo da recusa antiga; `blocking_requirements` contém as pendências reais atuais. Arquivos e campos originais não são alterados.

Se o ambiente só existir em `/etc/ponto-coleta/site.env`, o administrador pode executar a auditoria com um serviço transitório, ajustando **o caminho real da instalação**:

```bash
sudo systemd-run --wait --pipe --collect \
  --property=EnvironmentFile=/etc/ponto-coleta/site.env \
  --working-directory=/CAMINHO/DO/APLICATIVO \
  /CAMINHO/DO/APLICATIVO/.venv/bin/python scripts/audit_client_compliance.py --client-id 123
```

Não copie o arquivo de credenciais para o repositório e não imprima suas variáveis em diagnósticos. O relatório não contém os valores pessoais nem os segredos.

## Implantação

Não há novas variáveis nem dependências. Preserve todas as credenciais OAuth e Gmail atuais. Após o merge, na instalação e com o backup habitual e checkout limpo:

```bash
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart me-guarde.service
sudo systemctl status me-guarde.service --no-pager
```

Confirme o nome real do serviço; `me-guarde.service` é o exemplo adotado pelo projeto. A inicialização cria as tabelas e o campo de título novos, sem atualizar os aceites existentes. A Oracle não foi acessada nem reiniciada nesta tarefa.

## Verificação

A suíte cobre cadastro completo, legado sem metadados técnicos, formatos reais de assinatura, dados incompletos, assinatura ausente/corrompida, texto histórico indisponível, confirmação pendente, conta inativa, códigos inválidos/expirados/reutilizados, falha no provedor, atualização de dados e acesso ao Google após regularizar. Verifica também isolamento entre clientes, preservação literal do registro original e separação entre versões antigas e a nova série.

Os testes de navegador usam o provedor Google simulado e e-mail fictício, com SQLite temporário. Não substituem a homologação da conta relatada na Oracle. Aparelhos físicos e Safari/iOS não foram testados.

Resultado final desta revisão: **186 testes aprovados**, incluindo PostgreSQL 16 isolado, e **48 cenários de navegador** (24 de regularização e 24 de regressão do login Google). Dependências verificadas com `pip check`. A validação de assinaturas também rejeita imagens truncadas e limita a decodificação a 16 megapixels/2 MB, sem regravar os originais.
