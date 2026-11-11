/* Decorative enhancement only. Form values and payment actions stay server-owned. */
(() => {
  const collection = document.querySelector('#interface-icons');
  if (!collection) return;
  const symbols = new Map([...collection.content.querySelectorAll('[data-interface-icon]')]
    .map(item => [item.dataset.interfaceIcon, item.firstElementChild]));
  const rules = [
    [/copiar/i, 'copy'], [/consultar|verificar|atualizar/i, 'refresh'],
    [/comprovante|baixar|download/i, 'download'], [/voltar|anterior/i, 'back'],
    [/pix/i, 'pix'], [/dinheiro/i, 'wallet'],
    [/retir|entreg/i, 'pickup'], [/pacote|encomenda/i, 'box'],
    [/plano|crédito/i, 'wallet'], [/cadast|adicionar cliente/i, 'user-plus'],
    [/login|entrar|senha|acesso/i, 'key'], [/cliente|meus dados|quem retira|olá/i, 'person'],
    [/configura|preferência/i, 'settings'], [/histórico|pagamento|cobrança/i, 'payment'],
    [/salvar|gravar/i, 'save'], [/confirmar|concluir|aceitar/i, 'check-circle'],
    [/editar|alterar/i, 'edit'], [/excluir|remover/i, 'trash'],
    [/prazo|data|ciclo/i, 'calendar'], [/aviso|pendência|notifica/i, 'bell'],
    [/blog|artigo/i, 'blog'], [/contrato|termos|regras|privacidade/i, 'contract'],
    [/buscar|pesquis/i, 'search'], [/ajuda|dúvida/i, 'help'],
    [/métrica|indicador|painel/i, 'chart'], [/continuar|próximo/i, 'arrow-right'],
  ];
  function decorate(element, fallback) {
    if (element.querySelector('.ui-icon, img, svg') || element.closest('.article-body, .terms-text, [contenteditable]')) return;
    const label = element.textContent.trim();
    if (!label) return;
    const name = rules.find(([pattern]) => pattern.test(label))?.[1] || fallback;
    const symbol = symbols.get(name);
    if (!symbol) return;
    const decoration = symbol.cloneNode(true);
    decoration.dataset.interfaceDecoration = "";
    element.prepend(decoration);
  }
  document.querySelectorAll('main h1, .panel-card > h2, .customer-metrics h3')
    .forEach(element => decorate(element, 'box'));
  document.querySelectorAll('main button:not([data-slide-to]):not(.payment-grip):not(.clear-signature), main a.button, main details > summary')
    .forEach(element => decorate(element));
  document.querySelectorAll('.plan-checkout > p:first-of-type').forEach(element => decorate(element, 'payment'));
  document.querySelectorAll('[data-plan-status]').forEach(element => {
    // Polling rewrites this status text. CSS retains the state presentation.
    element.classList.add('payment-status');
  });
})();
