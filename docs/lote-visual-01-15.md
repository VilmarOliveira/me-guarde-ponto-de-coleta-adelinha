# Lote de ícones e navegação — novembro de 2026

Este lote corresponde às escolhas de interface posteriores ao PR 78.

1. Porta com seta nos acessos Entrar/Login.
2. Pacote com sino em Avisos de retirada.
3. Carteira com relógio em Pagar antes da retirada.
4. Google com elo rompido sobreposto ao canto superior direito no desvínculo.
5. Cadeado com setas em Alterar senha.
6. Cartão de endereço com localização em Dados para entrega e Ver dados para novas entregas.
7. Opções do formulário na ordem Pix Fast, Pix AI e Dinheiro; preserva a escolha preenchida e as configurações administrativas de disponibilidade.
8. Remoção do campo Data específica. Mês e ano e intervalo inclusivo permanecem. Links antigos de consulta por dia continuam funcionando e são apresentados como intervalo com início e fim iguais.
9. Coluna Ordem compacta no desktop; preserva a apresentação responsiva e a associação dos campos a cada modalidade.
10. Arquivar e restaurar solicitações do plano, com filtros Ativas, Arquivadas e Todas. Os registros financeiros, os créditos e o acompanhamento na API são preservados. As operações são administrativas, exigem CSRF e geram auditoria. Restaurar um pagamento recebido após arquivamento preserva a revisão e não concede créditos automaticamente.
11. Configurações no lugar de Gerenciar banners na Identidade visual, com um único acesso nessa área de ações.
12. Ícones escolhidos pela ação específica e por seu contexto. Símbolos genéricos deixam de ser usados para títulos sem correspondência. Logos e imagens de autoria dos artigos são preservados.
13. Blog mais limpo: criação de artigo em painel expansível, capa, formatação, biblioteca e buscadores opcionais recolhidos. Os campos e recursos continuam disponíveis. Índice e metadados do artigo ficam expansíveis.
14. Navegação comum e indicação de página para o site público, cliente e ponto. Atalhos de operação no painel respeitam as permissões. Configurações e tarefas administrativas menos frequentes ficam recolhidas. Formulários, alertas importantes e validação nativa continuam funcionais; campos inválidos dentro de painéis são revelados automaticamente.
15. Login com capitalização normal no cabeçalho mobile.

## Verificação

Os testes de negócio verificam filtros, auditoria, permissões, CSRF, restauração idempotente e preservação financeira. A verificação em Chromium usa um banco temporário e provedores falsos, cobre 390 e 1280 pixels, ícones aprovados, coluna compacta, navegação, salvamento de rascunho e arquivamento/restauração. Requisições externas do navegador são bloqueadas. As capturas produzidas pelo CI são evidência interna com dados fictícios.

O teste de interface não realiza cobranças, envia e-mails ou altera dados reais. A publicação na Oracle ocorre depois de mesclar o PR e reiniciar ponto-coleta.service.
