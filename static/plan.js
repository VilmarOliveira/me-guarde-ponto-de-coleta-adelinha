/* No payment or card fields are persisted in browser storage. */
(() => {
  const method = document.querySelector('[data-plan-method]');
  const card = document.querySelector('[data-plan-card]');
  if (method && card) method.addEventListener('change', () => {
    const visible = method.value === 'card';
    card.hidden = !visible; card.disabled = !visible;
  });
  const panel = document.querySelector('[data-plan-payment]');
  if (!panel) return;
  let timer = null, busy = false, stopped = false;
  const expiration = Date.parse(panel.dataset.planExpires || '');
  const hideExpired = () => {
    if (!Number.isFinite(expiration) || Date.now() < expiration) return;
    const image = panel.querySelector('[data-plan-pix]');
    if (image) image.closest('.plan-pix')?.setAttribute('hidden','');
    let note = panel.querySelector('[data-plan-expiry-note]');
    if (!note) {
      note = document.createElement('p'); note.className='notice';
      note.dataset.planExpiryNote=''; panel.querySelector('[data-plan-status]').after(note);
    }
    panel.querySelector('[data-plan-status]').textContent='QR Code vencido · Verificando cobrança';
    note.textContent='O prazo do QR Code terminou. Estamos verificando a situação na Fast. Consulte novamente antes de gerar outro Pix.';
  };
  if (Number.isFinite(expiration)) setTimeout(hideExpired, Math.min(2147483647,Math.max(0,expiration-Date.now())));
  hideExpired();
  const schedule = () => {
    clearTimeout(timer);
    if (!document.hidden && !stopped) timer = setTimeout(check, 10000);
  };
  async function check() {
    if (busy || document.hidden || stopped) return;
    busy = true; hideExpired();
    try {
      const response = await fetch(panel.dataset.planPayment, {headers: {'Accept': 'application/json'}, cache: 'no-store', signal: AbortSignal.timeout(15000)});
      if (response.redirected || [401,403].includes(response.status)) { stopped = true; return; }
      if (!response.ok) throw new Error('Retry later');
      const state = await response.json();
      if (state.done || (
        typeof state.pix_available === 'boolean'
        && state.pix_available !== Boolean(panel.querySelector('[data-plan-pix]'))
      )) { window.location.reload(); return; }
      panel.querySelector('[data-plan-status]').textContent = state.label;
    } catch (_) { /* Leave the existing order visible and permit manual retry. */ }
    finally { busy = false; schedule(); }
  }
  document.addEventListener('visibilitychange', () => { hideExpired(); schedule(); });
  window.addEventListener('pagehide', () => clearTimeout(timer));
  window.addEventListener('pageshow', schedule);
  schedule();
})();
