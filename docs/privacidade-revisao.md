# Política de Privacidade — entrega e revisão antes da publicação

## Endereços e navegação

- Página inicial: `https://pontodecoletaadl.servehttp.com/`
- Termos de Serviço: `https://pontodecoletaadl.servehttp.com/condicoes`
- Política de Privacidade: `https://pontodecoletaadl.servehttp.com/privacidade`

São os endereços para os campos correspondentes do Google Cloud **após merge, implantação e aprovação da política definitiva**. Não substituem a URI de retorno OAuth existente `/cliente/google/retorno`. Nenhuma credencial Google/Gmail ou variável da Oracle foi alterada.

`/privacidade` é pública e usa o mesmo layout responsivo do site. O rodapé compartilhado apresenta os dois documentos em todas as páginas que usam o template base. Termos aponta exclusivamente para o contrato já existente em `/condicoes`; não foi criado contrato paralelo nem modificada versão, assinatura ou prova de aceite.

Documentos abrem em nova aba, sem `opener` e sem enviar referrer, mantendo o formulário original em memória, inclusive senha e código digitados. O rascunho de cadastro continua guardando somente os campos previamente previstos, assinatura e estado de aceite vinculado à versão/hash; não guarda senha ou código. A abertura dos documentos funciona mesmo quando o armazenamento do navegador está bloqueado. Não foi acrescentado checkbox de consentimento com a política, publicidade ou métricas.

## Status da política

A entrega é uma **minuta explícita para revisão do responsável antes da publicação definitiva**. A data de atualização é 1º de novembro de 2026. A rota é acessível sem login, mas a minuta tem `noindex`. `PRIVACY_REVIEW_PENDING`, em `privacy_policy.py`, controla o aviso inicial e essa diretiva; não é variável de ambiente.

Não basta remover o aviso ou desativar a constante para considerar o texto aprovado. É preciso confirmar os fatos abaixo, revisar os trechos que os identificam como pendentes, conferir as fontes oficiais atuais e atualizar o texto/data conforme a aprovação efetiva. O código não foi implantado na Oracle por esta tarefa.

## Funcionamento confrontado com o código

| Tema | Evidência no repositório | O que foi descrito |
|---|---|---|
| Cadastro, cliente e retirada | `app.py`: registro, clientes, pacotes, pickups, recibos | Nome, CPF, contato, endereço, assinaturas, versões/textos, evidências e números de documento; não há upload de foto de identidade nesses fluxos. |
| Regularização e histórico | `client_regularization.py`, `client_compliance.py` | Novo aceite separado do anterior e confirmação adicional, sem substituir os originais. |
| Login Google | `google_login.py` | Authorization code + PKCE/OIDC; uso de `sub`, e-mail confirmado e claims técnicos; escopos `openid email`; sem perfil/foto/contatos ou leitura de caixa do cliente; senha no primeiro vínculo. |
| E-mail | `notifications.py` e rotas de códigos | Gmail API da conta de envio do estabelecimento, separada da autenticação de clientes; registro de status do envio. |
| Lembretes | `scripts/send_reminders.py` | Script existe, mas seu agendamento ativo não foi confirmado. |
| WhatsApp | Link em `base.html`, helper condicionado em `notifications.py` | Link acionado pelo visitante; não se afirmou que automação externa esteja ativa. Os fluxos atuais de confirmação usam e-mail. |
| Endereço | `static/app.js`: consulta ViaCEP | O navegador envia CEP ao ViaCEP e dados técnicos de conexão; nome/CPF/assinatura não estão na chamada. |
| Métricas | `app.py`: pageviews e `/metricas/evento` | Métricas próprias automáticas, identificador aleatório na sessão, páginas, dispositivo, origem e cliques selecionados. Não existe painel de consentimento/preferências para métricas no código. Não se alegou autorização já coletada. |
| Armazenamento local | `static/app.js`, Flask session, `static/sw.js` | Cookie de sessão assinado, rascunhos em sessionStorage e cache de recursos públicos; sem prometer criptografia de cookies/banco. |
| Proteção | Decoradores de permissão, CSRF, hashes, limites e OIDC | Apenas mecanismos implementados; HTTPS, logs e backups exigem configuração operacional. |
| Retenção | Rotas de desativação/exclusão e expiração dos códigos | Desativação preserva históricos; não há calendário geral de expurgo. Expirar o uso de um código não garante exclusão imediata de todos os registros ou backups. |

## Fontes oficiais e limitação da consulta

As referências usadas para estruturar os temas legais são:

- [LGPD — Lei 13.709/2018](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm): princípios, bases legais, transparência, dados sensíveis, conservação, direitos, transferências e proteção.
- [Marco Civil — Lei 12.965/2014](https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2014/lei/l12965.htm): obrigações que dependerão do enquadramento da operação. Não se atribuiu ao aplicativo uma rotina de guarda que não foi verificada.
- [Documentos e guias da ANPD](https://www.gov.br/anpd/pt-br/documentos-e-publicacoes), incluindo cookies.
- [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), incluindo Uso Limitado.
- [OpenID Connect do Google](https://developers.google.com/identity/openid-connect/openid-connect).
- [Verificação de marca OAuth](https://developers.google.com/identity/protocols/oauth2/production-readiness/branding-verification).

**Os acessos automatizados a esses sites retornaram HTTP 403/bloqueio de túnel neste ambiente, inclusive após tentativa com permissão ampliada.** Não se afirma ter conferido a redação online mais recente em 1º/11/2026. O texto foi preparado com referências normativas identificadas e confronto com o código; a consulta atualizada e a revisão jurídica/operacional continuam pendentes antes de aprovar a publicação. A limitação também está declarada na própria minuta.

## Confirmações necessárias do responsável

1. Prestadores realmente contratados e regiões: a documentação anterior cita Oracle e Neon, mas a instalação e os contratos não foram acessados. Confirmar também backups e subprocessadores/transferências internacionais.
2. Critérios e prazos de conservação por categoria: contratos, retiradas/pagamentos, usuários, logs, métricas, tentativas/códigos e backups. Não foi inventado período de retenção.
3. Avaliação da base legal das métricas próprias, necessidade/balanceamento e mecanismos de escolha/consentimento caso exigíveis. Um texto de política, isoladamente, não cria consentimento nem implementa esses controles.
4. Processo de atendimento aos direitos, resposta a incidentes e controles operacionais. Não se prometeu exclusão automática ou segurança absoluta.
5. Eventuais tratamentos fora do aplicativo: câmeras, cópias de documentos, canais de atendimento, crianças/adolescentes e campanhas. Não foram presumidos como existentes.
6. Enquadramento da assinatura, se utilizada como biometria, e adequação da base legal de dados sensíveis. Legítimo interesse não foi usado como base genérica para dados sensíveis.

## Implantação

Após revisão e merge, na pasta real do aplicativo e com checkout limpo, use o procedimento habitual:

```bash
git pull --ff-only origin main
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart me-guarde.service
sudo systemctl status me-guarde.service --no-pager
```

Confirme o nome real do serviço; `me-guarde.service` é o exemplo do projeto. Não altere `/etc/ponto-coleta/site.env`, credenciais, retorno OAuth ou variáveis existentes para esta atualização. Não há migração de dados nem dependência nova. Confira as duas URLs públicas sem login antes de preencher os campos no Google Cloud.

## Verificação

Testes funcionais cobrem acesso público, identidade/contato/data, links em páginas públicas e autenticadas, ausência de novo consentimento genérico, rejeição de retorno externo e preservação dos contratos/clientes existentes.

`tests/browser/privacy.cjs`, usando o servidor isolado `tests/browser/serve_responsive.py`, confere 320, 360, 390, 430, 768, 1024 e 1440 px. São 28 verificações de página/largura e 28 fluxos de documentos, incluindo dados, senha, código, aceite e assinatura preservados na aba original; rascunho sem senha/código; acesso com sessionStorage bloqueado; ausência de rolagem lateral na página. Os testes não enviam e-mails nem acessam dados reais. Capturas e relatório ficam em `/tmp/adelinha-privacy-results`.

Chromium emulado; dispositivos físicos e Safari/iOS não foram testados. Os sites oficiais e a configuração efetiva de produção exigem a conferência indicada acima.

Resultado desta revisão: **183 testes funcionais aprovados**; os 6 testes opcionais de PostgreSQL não foram executados nesta alteração de páginas/links, que não modifica o banco. No navegador, **28 verificações de página/largura e 28 fluxos de documentos** passaram. `pip check` e verificações de sintaxe não apontaram problemas.
