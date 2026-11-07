# Verificação de responsividade

Execute a partir da raiz do repositório. O servidor usa um banco temporário próprio, dados fictícios e envio de e-mail desativado. Ele não usa `DATABASE_URL` nem acessa dados de produção. As credenciais no código são exclusivas das fixtures locais.

## Preparar e executar

Python e as dependências de `requirements.txt` devem estar disponíveis na `.venv`. Para os testes de navegador, instale as dependências de desenvolvimento fora do checkout:

```bash
npm install --prefix /tmp/adelinha-browser --cache /tmp/adelinha-npm-cache playwright jsqr
```

Em um terminal, inicie o servidor de fixtures (somente loopback, porta 5001):

```bash
.venv/bin/python tests/browser/serve_responsive.py
```

Em outro terminal:

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/responsiveness.cjs
```

O teste usa Chromium em `/usr/bin/chromium`. Para outra instalação local, defina `CHROMIUM_PATH`. O teste rejeita endereços de servidor externos.

## Cobertura e resultados

- Larguras: **320, 360, 390, 430, 768, 1024 e 1440 px**.
- Perfis: anônimo, administrador, gerente, atendente, cliente com pacotes e cliente sem pacotes.
- Páginas de cadastro, login, recuperação, condições/contrato, sucesso, painéis, clientes, pacotes, retirada/Pix, comprovantes, configurações, temas, usuários, notificações, métricas, blog e dúvidas.
- Menus abertos, formulários de edição expandidos, mensagens extensas, estados vazios, nomes e conteúdo longo.
- Verificação de largura real da página sem mascaramento por `overflow-x:hidden`, controles fora do viewport, cabeçalho, alvos de toque, fontes de campos e contêineres de tabelas.
- QR Code decodificado e comparado ao código copiado para a área de transferência.
- Assinatura por toque no cadastro e por mouse na retirada; campos, confirmação de pagamento, seleção de pacotes, código Pix e bitmap da assinatura são comparados antes/depois de redimensionar e girar a tela.
- Carrossel com intervalo de três segundos, indicadores e toque, sem setas ou botão Pausar.
- WhatsApp flutuante sem cobrir controles ou imagens durante a rolagem.
- Confirmações nativas são canceladas; nenhum cadastro é excluído por esses testes.
- Impressão do comprovante, download do PDF original, contrato e proporção do contêiner de logo.

O relatório JSON, capturas e PDF de impressão ficam em `/tmp/adelinha-responsive-results` (configurável por `RESPONSIVE_REPORT_DIR`). Uma falha retorna código de saída diferente de zero. As asserções de interação interrompem o teste quando o comportamento esperado não é observado; verifique também o código de saída, além do relatório parcial.

Para repetir somente as verificações de interação após corrigir um teste, preservando os cenários de uma matriz já concluída:

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/responsiveness.cjs --only-interactions
```

O arquivo de relatório anterior deve pertencer à execução e versão que está sendo verificada. Não use um relatório antigo para atestar uma alteração posterior sem executar os cenários afetados.

## Limites

A verificação é de Chromium com telas e toque emulados. Ela não substitui testes em aparelhos físicos ou em Safari/iOS. Caixas nativas de confirmação são controladas pelo navegador, não pelo CSS do site. Os serviços externos não recebem mensagens, pagamentos ou outros dados durante a execução.

Para verificar especificamente a configuração e ampliação dos quatro cartões, com o mesmo servidor de fixtures:

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/explanatory_cards.cjs
```

O teste usa somente `127.0.0.1:5001`, publica um cartão fictício no banco temporário e verifica as sete larguras, miniaturas/original, upload, cancelamento, cache, teclado e toque.

Faixa das 16 lojas e botão de cadastro (mesmo servidor isolado):

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/stores_signup.cjs
```

Verifica visitante, cliente e administrador nas sete larguras, incluindo menus, logout, navegação ao cadastro, logos, movimento contínuo, pausa e movimento reduzido. Relatório e capturas em `/tmp/adelinha-stores-results`.

Retomada e intervalo dos banners (mesmo servidor isolado):

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/carousel_resume.cjs
```

Usa relógio controlado para comprovar os três segundos completos e eventos de ciclo de vida simulados em computador/celular. Inclui suspensão real de JavaScript no Chromium via CDP. Relatório em `/tmp/adelinha-carousel-results/report.json`. Limites de validação física em `docs/retomada-carrossel.md`.


Contrato v3 e login com Google usam um servidor isolado próprio, sem acesso real ao Google nem envio de e-mails:

```bash
.venv/bin/python tests/browser/serve_google_login.py
# Em outro terminal:
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/google_login.cjs
```

São 24 cenários de navegador em 320, 390, 768 e 1440 px; a suíte pytest valida tokens RSA pela biblioteca oficial. O provedor visual é simulado. O teste também verifica fallback por senha, PDF vigente e rascunho antigo que exige novo aceite da v3, preservando os dados preenchidos.

Regularização e nova série do contrato, com o mesmo servidor local de Google:

```bash
.venv/bin/python tests/browser/serve_google_login.py
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/client_regularization.cjs
```

Verifica 24 combinações de largura/cadastro (320, 390, 768 e 1440 px): completo, legado, dados faltantes, assinatura ausente, e-mail pendente e inativo. Inclui desenho por toque, preservação no redimensionamento/envio de código, regularização e entrada pelo Google. Relatório em `/tmp/adelinha-compliance-results`.

Política de Privacidade e preservação do cadastro (servidor `serve_responsive.py`):

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/privacy.cjs
```

Confere as duas páginas públicas, rodapés, novas abas, dados/senha/código/assinatura mantidos no formulário e rascunho sem senha/código, inclusive com armazenamento bloqueado, nas sete larguras. Resultados em `/tmp/adelinha-privacy-results`.

Central de pendências e autorizações (servidor isolado, clientes fictícios):

```bash
.venv/bin/python tests/browser/serve_client_actions.py
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/client_actions.cjs
```

O teste usa as sete larguras, publicação pelo administrador, resposta expressa/recusa, assinatura por toque/mouse, códigos de e-mail simulados, restrições no servidor, versões, cancelamento e ambos os logins. Relatório e capturas em `/tmp/adelinha-actions-results`. Não conecta ao Google nem envia e-mails reais.

Novo aceite da revisão contratual já publicada (mesmo servidor `serve_client_actions.py`, com banco temporário novo):

```bash
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/contract_reacceptance.cjs
```

Publica um contrato informativo, ativa depois a exigência do texto já vigente e verifica clientes antigos por senha/Google, título visual idêntico com revisão diferente, assinatura e código, histórico e repetição sem duplicidade em 320, 390, 768 e 1440 px. Relatório em `/tmp/adelinha-reaccept-results`.

Organização da Área do Cliente e formulário de aceite (servidor isolado próprio):

```bash
.venv/bin/python tests/browser/serve_acceptance_layout.py
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/acceptance_layout.cjs
```

Verifica as sete larguras, ordem visual, checkbox verde com seleção pelo texto, toque/mouse, falha do provedor, sucesso com endereço oculto, envio/reenvio sem perda, código incorreto/expirado, checkbox obrigatório, gravação individual e histórico. O servidor de testes simula a passagem do tempo e o provedor; não há e-mails reais. Relatório em `/tmp/adelinha-acceptance-results`.

Blog com ícones, conteúdo longo e acervo antigo:

```bash
.venv/bin/python tests/browser/serve_blog_icons.py
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/blog_icons.cjs
```

O servidor usa banco temporário e integrações desativadas. Verifica as sete larguras, artigos com mais de 8.000 caracteres, equivalência da prévia, busca/inserção/troca/remoção de marcações, contadores, upload PNG/WebP, substituição, arquivamento que preserva artigos, índice, links, segurança e capas antigas. Relatório em `/tmp/adelinha-blog-results`.

Capa opcional nos dois modelos do blog:

```bash
.venv/bin/python tests/browser/serve_blog_covers.py
NODE_PATH=/tmp/adelinha-browser/node_modules node tests/browser/blog_covers.cjs
```

O servidor é isolado, com banco e imagens fictícios. Verifica as sete larguras, PNG/JPG/WebP, imagem completa horizontal/vertical, prévia, descrição acessível, substituição, rejeição com preservação da capa anterior, remoção e artigo sem capa, mantendo conteúdo e SEO. Relatório em `/tmp/adelinha-blog-cover-results`.

## Plano ME GUARDE Mais / FastFlow

Teste independente com banco temporário, servidor somente em `127.0.0.1:5058`,
credenciais fictícias e API FastFlow substituída **apenas na fixture**. Não usa o
banco de produção, não envia e-mails nem faz cobranças reais. Instale a biblioteca
de desenvolvimento fora do checkout:

```bash
.venv/bin/python -m pip install --target /tmp/adelinha-browser-py playwright
PYTHONPATH=/tmp/adelinha-browser-py .venv/bin/python tests/browser/plan_fastflow.py
```

Usa `/usr/bin/chromium`, verifica o plano em 320, 360, 390, 430, 768, 1024 e
1440 px; seção pública de preços, retorno ao plano após login, bloqueio do cartão, QR Code Pix e botão de cópia por toque em contexto
móvel, ativação, renovação antecipada sem liberação de créditos futuros e painel
administrativo. Confere ausência de rolagem horizontal e erros de JavaScript e
ausência de campos de cartão na oferta atual por Pix. Capturas e
banco fictício ficam no diretório temporário indicado no JSON final. A emulação
não substitui aparelhos físicos, Safari/iOS ou homologação real autorizada do
provedor. O teste exige a porta 5058 livre.

Validação manual e cancelamento administrativo do plano, com servidor próprio em
`127.0.0.1:5059`, banco temporário e provedor simulado:

```bash
PYTHONPATH=/tmp/adelinha-browser-py .venv/bin/python tests/browser/plan_administration.py
```

Confere sete larguras, busca de clientes, prévia/confirmacão com motivo, liberação
administrativa sem chamada de cobrança, cancelamento não pago por toque,
histórico separado e ausência de rolagem horizontal/erros JavaScript. Os artefatos
ficam no diretório temporário indicado no JSON final. Não usa dados de produção,
não envia e-mails e não executa cobranças reais.

## Área do Cliente e três modalidades de pagamento

```bash
.venv/bin/python -m pip install --target /tmp/adelinha-browser-py playwright
PYTHONPATH=/tmp/adelinha-browser-py .venv/bin/python tests/browser/customer_payments.py
```

Usa Chromium em `/usr/bin/chromium`, servidor temporário em `127.0.0.1:5077`, dados fictícios e FastFlow simulada. Verifica as seis abas nas sete larguras, menu móvel, saldo, PDF, Pix AI/Fast/Dinheiro, pagamento separado da entrega, assinatura no redimensionamento, renovação manual e ativação por API. Relatório e capturas ficam na pasta temporária informada ao final. Sem cobranças ou e-mails reais.

O cenário `customer_payments.py` também verifica a prévia de cobertura (1/2 créditos, sem débito), sua identificação na retirada e a habilitação do Pix Fast em R$ 3,99, R$ 4,00, R$ 1.000,00 e R$ 1.000,01.

## Pagamento antecipado

```bash
PYTHONPATH=/tmp/adelinha-browser-py .venv/bin/python tests/browser/advance_payments.py
```

Fixture isolada em `127.0.0.1:5081`, com API simulada. Verifica seleção de pacotes, prévia, cobertura, geração pelo cliente, QR/Copia e Cola, quitação sem entrega, retirada com saldo zero e PDF. Confere ausência de rolagem horizontal em sete larguras. Veja também `docs/pagamento-antecipado.md`.
