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
  let attempts = 0, timer = null, busy = false;
  const schedule = () => {
    clearTimeout(timer);
    if (!document.hidden && attempts < 60) timer = setTimeout(check, 10000);
  };
  async function check() {
    if (busy || document.hidden || attempts >= 60) return;
    busy = true; attempts++;
    try {
      const response = await fetch(panel.dataset.planPayment, {headers: {'Accept': 'application/json'}, cache: 'no-store', signal: AbortSignal.timeout(15000)});
      if (!response.ok || response.redirected) { attempts = 60; return; }
      const state = await response.json();
      if (state.done) { window.location.reload(); return; }
      panel.querySelector('[data-plan-status]').textContent = state.label;
    } catch (_) { /* Leave the existing order visible and permit manual retry. */ }
    finally { busy = false; schedule(); }
  }
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('pagehide', () => clearTimeout(timer));
  window.addEventListener('pageshow', schedule);
  schedule();
})();
