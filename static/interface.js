/* Presentation only: labels, routes, form values and business actions stay server-owned. */
(() => {
  const collection = document.querySelector('#interface-icons');
  if (!collection) return;
  const symbols = new Map([...collection.content.querySelectorAll('[data-interface-icon]')]
    .map(item => [item.dataset.interfaceIcon, item.firstElementChild]));
  // Specific actions take precedence over objects mentioned in their labels.
  const rules = [
    [/^desvincular.*google/i, 'google-unlink'],
    [/^avisos? de retirada/i, 'parcel-notice'],
    [/^pagar antes da retirada|^pagamento antecipado/i, 'wallet-clock'],
    [/^(alterar|redefinir|recuperar|criar|validar.*alterar).*senha/i, 'password-change'],
    [/^dados para (novas )?entrega|^ver dados para novas entregas/i, 'address-card'],
    [/^restaurar.*(cobran|solicita)|^desarquivar/i, 'unarchive'],
    [/^arquivar|^arquivadas$/i, 'archive'],
    [/^voltar|^anterior|^solicitações anteriores/i, 'back'],
    [/^prévia|^pré-visualizar|^visualizar|^ver detalhes/i, 'eye'],
    [/^cancelar|^fechar/i, 'close'],
    [/^resolver.*pendência/i, 'clipboard'],
    [/^(cadastrar|adicionar|registrar|receber).*pacote/i, 'parcel-in'],
    [/^enviar.*(imagem|ícone|foto)/i, 'upload'],
    [/^copiar/i, 'copy'], [/^baixar|^download/i, 'download'],
    [/^buscar|^pesquisar/i, 'search'], [/^limpar filtros|^filtrar/i, 'filter'],
    [/^salvar|^gravar/i, 'save'], [/^editar|^alterar/i, 'edit'],
    [/^excluir|^remover|^retirar da publicação/i, 'trash'],
    [/^enviar|^envio de códigos/i, 'send'], [/^publicar/i, 'send'],
    [/^consultar|^verificar|^atualizar|^testar autorização/i, 'refresh'],
    [/^entrar|^login/i, 'login'], [/^sair|^encerrar sessão/i, 'logout'],
    [/^concluir retirada|^confirmar entrega/i, 'parcel-out'],
    [/^confirmar|^concluir|^aceitar|^validar|^assinar/i, 'check-circle'],
    [/^continuar|^próxim|^solicitações seguintes/i, 'arrow-right'],
    [/^adicionar cliente|^cadastre|^cadastrar|^cadastro|^novo cliente/i, 'user-plus'],
    [/^novo artigo|^escrever|^editor do blog/i, 'edit'],
    [/^gerar.*pix|^pague com pix|^pagar com pix|^pix/i, 'pix'],
    [/^pague com dinheiro|^pagar em dinheiro|^dinheiro/i, 'wallet'],
    [/^aguardando retirada$/i, 'parcel-waiting'], [/^pacotes retirados$/i, 'parcel-collected'],
    [/^histórico|^retirada #/i, 'calendar'],
    [/^configurações|^preferências|^aparência/i, 'settings'],
    [/^blog|^artigos|^ler artigo|^guia/i, 'book-open'],
    [/^dados pessoais|^meus dados|^editar meus dados|^quem retira|^olá/i, 'person'],
    [/^cliente|^usuários|^equipe/i, 'users'],
    [/^área do cliente/i, 'client-area'], [/^área do ponto/i, 'store'],
    [/^início|^página inicial/i, 'home'],
    [/^plano|^meu plano|^créditos/i, 'wallet'],
    [/^pagamento|^solicitações e pagamentos|^cobrança|^ver pagamento/i, 'payment'],
    [/^comprovante/i, 'download'],
    [/^contrato|^termos|^regras|^privacidade|^autoriza/i, 'contract'],
    [/^endereço|^dados para entrega/i, 'address-card'],
    [/^banners|^gerenciar banners/i, 'banner'],
    [/^identidade visual|^fotos|^imagem|^cartões explicativos/i, 'image'],
    [/^prazo|^data|^ciclo/i, 'calendar'],
    [/^aviso|^pendência|^notificação/i, 'bell'],
    [/^e-mail|^gmail/i, 'mail'],
    [/^métrica|^indicador|^painel/i, 'chart'],
    [/^ajuda|^dúvida|^perguntas frequentes/i, 'help'],
    [/^concluir retirada|^retirar|^entregar|^confirmar entrega/i, 'parcel-out'],
    [/^pacote|^meus pacotes|^ver meus pacotes|^encomenda|^consulte seus pacotes/i, 'box'],
  ];
  function decorate(element) {
    if (element.closest('.article-body, .terms-text, [contenteditable], .blog-icon-library, .blog-custom-icons')) return;
    if (/^voltar|^anterior/i.test(element.textContent.trim())) {
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) node.textContent = node.textContent.replace(/[←⬅]+\s*/gu, '');
    }
    const label = element.textContent.trim().replace(/\s+/g, ' ');
    const name = element.dataset.uiIcon || rules.find(([pattern]) => pattern.test(label))?.[1];
    const source = symbols.get(name);
    if (!source) return; // Avoid unrelated generic icons on unknown headings.
    const existing = element.querySelector(':scope > svg.ui-icon');
    if (existing?.dataset.icon === name) return;
    if (!existing && element.querySelector('svg, img')) return; // Preserve brand and author-provided images.
    const decoration = source.cloneNode(true);
    decoration.dataset.interfaceDecoration = '';
    if (existing) existing.replaceWith(decoration);
    else element.prepend(decoration);
  }
  document.querySelectorAll('main a:not(.button)').forEach(link => {
    if (/^voltar/i.test(link.textContent.trim()) && !link.closest('.article-body, .terms-text')) link.classList.add('button', 'ghost');
  });
  document.querySelectorAll('main h1, main h2, .customer-metrics h3, main button:not([data-slide-to]):not(.payment-grip):not(.clear-signature), main a.button, main details > summary, [data-ui-icon]')
    .forEach(decorate);
  const heading = document.querySelector('main h1');
  const current = document.querySelector('[data-page-current]');
  if (heading && current) {
    current.textContent = heading.textContent.trim(); current.hidden = false;
    document.querySelector('[data-page-separator]').hidden = false;
  }
  document.querySelectorAll('[data-open-panel]').forEach(link => link.addEventListener('click', () => {
    const panel = document.getElementById(link.dataset.openPanel);
    if (panel?.tagName === 'DETAILS') panel.open = true;
  }));
  document.querySelectorAll('[data-plan-status]').forEach(element => element.classList.add('payment-status'));
  // Native required-field validation must remain usable inside optional panels.
  document.addEventListener('invalid', event => {
    for (let panel = event.target.closest('details'); panel; panel = panel.parentElement?.closest('details')) panel.open = true;
  }, true);
  document.querySelectorAll('.section-tabs a, .customer-tabs a, .site-nav a').forEach(link => {
    if (link.href === location.href) link.setAttribute('aria-current', 'page');
  });
})();
