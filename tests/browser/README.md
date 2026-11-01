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
