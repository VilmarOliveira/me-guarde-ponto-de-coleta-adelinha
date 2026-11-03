# Página inicial do plano e teste de webhook FastFlow

Atualização de 3 de novembro de 2026.

## Alterações

- Webhook em `plan_routes.py`: mantém configuração, limite de 64 KB e HMAC; depois verifica JSON objeto e aceita exclusivamente `test is True` com HTTP 200, antes de exigir ID ou tocar na API/banco financeiro. Teste de conectividade não é pagamento nem evidência para liberar plano. Eventos reais mantêm ID, API, valor, provedor, callback de vínculo e concessão idempotente.
- A seção antiga “Preços por pacote” foi substituída por HTML em `templates/home_plan_pricing.html`, incluído uma única vez em `templates/index.html`. Título, mensalidade, vantagens, separador OU, preços avulsos, limites, ressalva e botão são elementos reais e acessíveis, sem o rodapé da simulação.
- Preços/limites vêm de constantes de `core.py`, usadas também na classificação/cobrança; mensalidade e créditos vêm do serviço de plano. `public_offer()` só prepara os dados de apresentação e calcula o percentual conservador de economia com o uso completo (31%). Não cria cobrança ou outra regra de faturamento.
- Estilos centralizados em `static/responsive.css`, mantendo Montserrat, laranja, verde-água, grafite, branco, bordas arredondadas e ícones vetoriais. Desktop com duas colunas; abaixo de 768 px, blocos empilhados. A arte de referência não é inserida como imagem da seção.
- Botão “Assinar plano” vai a `/plano/assinar`. Cliente autenticado segue para `/cliente/plano`, que mostra ciclo, saldo e renovação conforme as regras. Visitante vai ao login; a sessão registra somente um destino interno fixo e o consome após login por senha ou Google, inclusive no primeiro vínculo seguro. Falha de senha preserva o retorno; login normal continua na Área do Cliente. Não aceita URL externa de retorno. Acesso da equipe mantém sessão e encaminha ao painel, sem atribuir conta/permissões de cliente.
- Oferta pública e checkout somente por **Pix**. `CARD_CHECKOUT_AVAILABLE=False` bloqueia cartão no servidor mesmo com variáveis antigas ativas. Não apaga pedidos, bandeira/final, ciclos ou código de integração anteriores. Testes técnicos legados de Card In só reabilitam a capacidade por monkeypatch dentro da própria fixture; isso não constitui caminho de ativação em produção.
- Ícone Pix: Font Awesome Free 6.7.2, copyright 2024 Fonticons, Inc., CC BY 4.0, fonte `https://github.com/FortAwesome/Font-Awesome/blob/6.x/svgs/brands/pix.svg`, crédito preservado no SVG inline de `templates/icons.html`. Nenhum carregamento externo do ícone no navegador.

## Logo original

O anexo disponível mostra a referência do layout, mas não disponibiliza um arquivo separado da logo original ME GUARDE⁺. A aplicação está preparada para usar **o arquivo original inteiro** configurado em `ME_GUARDE_MAIS_LOGO_PATH` (já existente), sem alterar cores, desenho, proporção ou cortar. Até disponibilizar esse arquivo, exibe apenas o nome textual; não redesenha a caixa, as mãos ou o coração a partir da simulação.

Quando o arquivo aprovado estiver disponível, copie-o para o caminho persistente já configurado, com leitura para o usuário do serviço. PNG/JPG/WebP até 900 KB, conforme o endpoint existente. Se a variável não estava configurada, ajuste apenas `ME_GUARDE_MAIS_LOGO_PATH` em `/etc/ponto-coleta/site.env` e reinicie o serviço. Não altere credenciais FastFlow, Google/Gmail, origem, segredo ou timers. A confirmação visual com a logo exata depende desse arquivo.

## Implantação

Não há migração de dados específica nem nova dependência. Faça o backup e o procedimento de atualização já adotado. Após revisão/merge, na pasta real do checkout Oracle:

```bash
git status --short
git switch main
git pull --ff-only origin main
.venv/bin/python -m pip check
sudo systemctl restart ponto-coleta.service
sudo systemctl status ponto-coleta.service --no-pager
```

Preserve `/etc/ponto-coleta/site.env`, banco persistente e os timers de reconciliação/lembretes já configurados. Não é necessário recriar webhook, segredo, agendamentos ou clientes. A atualização apenas passa a reconhecer o teste assinado do painel. A Oracle não foi acessada por esta tarefa.

## Validação

**314 testes aprovados** na suíte completa com SQLite temporário e PostgreSQL 16 em banco novo isolado. Testes automatizados usam banco temporário, dados fictícios e APIs/e-mails simulados. Cobrem o payload exato do painel, HMAC inválido, JSON inválido/não objeto, diferença entre `true`, `1` e `"true"`, limite/configuração, ausência de alterações financeiras no teste, eventos reais/duplicados, preços do servidor, botão autenticado/deslogado, senha incorreta e retorno após senha/Google, login normal, proteção contra retorno externo e recusa de cartão no servidor mesmo com variáveis antigas em `1`.

O teste de navegador em `tests/browser/plan_fastflow.py` também confere a seção pública em 320, 360, 390, 430, 768, 1024 e 1440 px, duas colunas/empilhamento, ausência de rolagem horizontal, login retornando ao plano, checkout só Pix, toque para copiar, ativação, renovação antecipada e administração. É Chromium com emulação móvel, não aparelho físico ou Safari/iOS. Capturas em `/tmp` são somente de dados fictícios.
