document.querySelectorAll('form[method="post"], form[method="POST"]').forEach((form) => {
  if (!form.querySelector('[name="csrf_token"]')) {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.name = 'csrf_token';
    input.value = document.querySelector('meta[name="csrf-token"]')?.content || '';
    form.appendChild(input);
  }
});

document.querySelectorAll('[data-signature-form]').forEach((form) => {
  const canvas = form.querySelector('canvas.signature');
  const hidden = form.querySelector('input[name="signature"], input.signature-value');
  const ctx = canvas.getContext('2d');
  let drawing = false;
  let moved = false;
  const point = (event) => {
    const bounds = canvas.getBoundingClientRect();
    const pointer = event.touches?.[0] || event;
    return {
      x: (pointer.clientX - bounds.left) * canvas.width / bounds.width,
      y: (pointer.clientY - bounds.top) * canvas.height / bounds.height,
    };
  };
  const start = (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if (event.pointerId !== undefined) canvas.setPointerCapture(event.pointerId);
    drawing = true;
    const position = point(event);
    ctx.beginPath();
    ctx.moveTo(position.x, position.y);
    event.preventDefault();
  };
  const move = (event) => {
    if (!drawing) return;
    const position = point(event);
    ctx.lineWidth = 2.5 * canvas.width / canvas.getBoundingClientRect().width;
    ctx.lineCap = 'round';
    ctx.strokeStyle = '#282620';
    ctx.lineTo(position.x, position.y);
    ctx.stroke();
    moved = true;
    hidden.value = canvas.toDataURL('image/png');
    event.preventDefault();
  };
  const stop = () => { drawing = false; };
  form.restoreSignature = (dataUrl) => {
    if (!dataUrl?.startsWith('data:image/')) return;
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(canvas.width / image.width, canvas.height / image.height);
      const width = image.width * scale;
      const height = image.height * scale;
      ctx.drawImage(image, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
      moved = true;
      hidden.value = dataUrl;
    };
    image.src = dataUrl;
  };
  // O bitmap mantém suas dimensões lógicas: redimensionar a tela não o apaga.
  if ('PointerEvent' in window) {
    canvas.addEventListener('pointerdown', start);
    canvas.addEventListener('pointermove', move);
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => canvas.addEventListener(name, stop));
  } else {
    ['mousedown', 'touchstart'].forEach(name => canvas.addEventListener(name, start, {passive: false}));
    ['mousemove', 'touchmove'].forEach(name => canvas.addEventListener(name, move, {passive: false}));
    ['mouseup', 'mouseleave', 'touchend', 'touchcancel'].forEach(name => canvas.addEventListener(name, stop));
  }
  window.addEventListener('resize', stop);
  form.querySelector('.clear-signature')?.addEventListener('click', () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    moved = false;
    hidden.value = '';
  });
  if (form.hasAttribute('data-restore-signature') && hidden.value) form.restoreSignature(hidden.value);
  form.addEventListener('submit', (event) => {
    if (event.submitter?.matches('[data-send-email]')) return;
    if (!moved) {
      event.preventDefault();
      alert('Faça sua assinatura antes de continuar.');
      return;
    }
    hidden.value = canvas.toDataURL('image/png');
  });
});

document.querySelectorAll('[data-copy]').forEach((button) => button.addEventListener('click', async () => {
  const target = document.querySelector(button.dataset.copy);
  await navigator.clipboard.writeText(target.value || target.innerText);
  const previous = button.innerText;
  button.innerText = 'Copiado!';
  setTimeout(() => { button.innerText = previous; }, 1500);
}));

function validCpf(value) {
  const numbers = value.replace(/\D/g, '');
  if (numbers.length !== 11 || /^(\d)\1{10}$/.test(numbers)) return false;
  for (const length of [9, 10]) {
    let total = 0;
    for (let index = 0; index < length; index += 1) {
      total += Number(numbers[index]) * (length + 1 - index);
    }
    let digit = 11 - (total % 11);
    if (digit >= 10) digit = 0;
    if (Number(numbers[length]) !== digit) return false;
  }
  return true;
}

document.querySelectorAll('[data-cpf]').forEach((field) => {
  const validate = () => field.setCustomValidity(
    validCpf(field.value) ? '' : 'CPF inválido. Confira os números digitados.',
  );
  field.addEventListener('input', () => {
    const numbers = field.value.replace(/\D/g, '').slice(0, 11);
    field.value = numbers.replace(/(\d{3})(\d)/, '$1.$2')
      .replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d{1,2})$/, '$1-$2');
    validate();
  });
  field.form?.addEventListener('submit', validate);
});

// Busca residencial pelo CEP. Cada nova alteração cancela a requisição anterior
// e limpa os dados associados, evitando misturar endereços de CEPs diferentes.
document.querySelectorAll('[data-cep-form]').forEach((container) => {
  const cepInput = container.querySelector('[data-cep]');
  const status = container.querySelector('[data-cep-status]');
  const fields = {
    residential_street: container.querySelector('[name="residential_street"]'),
    residential_district: container.querySelector('[name="residential_district"]'),
    residential_city: container.querySelector('[name="residential_city"]'),
    residential_state: container.querySelector('[name="residential_state"]'),
  };
  let controller;
  let lastCep = cepInput.value.replace(/\D/g, '');
  const clearAddress = () => Object.values(fields).forEach((field) => { field.value = ''; });
  cepInput.addEventListener('input', () => {
    const digits = cepInput.value.replace(/\D/g, '').slice(0, 8);
    cepInput.value = digits.replace(/(\d{5})(\d)/, '$1-$2');
    controller?.abort();
    if (digits !== lastCep) clearAddress();
    lastCep = digits;
    status.textContent = digits.length ? 'Informe os 8 dígitos do CEP.' : '';
    status.className = 'cep-status';
    if (digits.length !== 8) return;
    controller = new AbortController();
    status.textContent = 'Buscando endereço...';
    status.classList.add('loading');
    fetch(`https://viacep.com.br/ws/${digits}/json/`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Falha na consulta');
        return response.json();
      })
      .then((data) => {
        if (cepInput.value.replace(/\D/g, '') !== digits) return;
        if (data.erro || !data.logradouro || !data.bairro || !data.localidade || !data.uf) {
          throw new Error('CEP incompleto');
        }
        fields.residential_street.value = data.logradouro;
        fields.residential_district.value = data.bairro;
        fields.residential_city.value = data.localidade;
        fields.residential_state.value = data.uf;
        status.textContent = 'Endereço encontrado. Confira os dados e informe o número.';
        status.className = 'cep-status success';
        container.querySelector('[name="residential_number"]')?.focus();
      })
      .catch((error) => {
        if (error.name === 'AbortError') return;
        if (cepInput.value.replace(/\D/g, '') !== digits) return;
        clearAddress();
        status.textContent = 'CEP não encontrado ou consulta indisponível. Preencha ou corrija o endereço manualmente.';
        status.className = 'cep-status error';
      });
  });
});

const clientId = document.querySelector('#client-id');
const clientLabel = document.querySelector('#client-label');
const clientResults = document.querySelector('#package-client-results');
const clientSearch = document.querySelector('#package-client-search');

function selectClient(id, name, publicId) {
  if (!clientId || !clientLabel) return;
  clientId.value = id;
  clientLabel.value = `${name} · ${publicId}`;
  clientLabel.classList.add('selected-client');
  if (clientResults) clientResults.replaceChildren();
}

// Os resultados da busca superior continuam podendo preencher o formulário.
document.querySelectorAll('[data-client-id]').forEach((button) => button.addEventListener('click', () => {
  const [name, publicId] = button.dataset.clientLabel.split(' · ');
  selectClient(button.dataset.clientId, name, publicId);
  clientLabel?.scrollIntoView({ behavior: 'smooth', block: 'center' });
}));

if (clientSearch && clientResults) {
  let requestNumber = 0;
  let timer;
  clientSearch.addEventListener('input', () => {
    clearTimeout(timer);
    // Alterar a pesquisa invalida uma seleção anterior para evitar vínculo acidental.
    clientId.value = '';
    clientLabel.value = '';
    clientLabel.classList.remove('selected-client');
    const query = clientSearch.value.trim();
    clientResults.replaceChildren();
    if (!query) return;
    const currentRequest = ++requestNumber;
    timer = setTimeout(async () => {
      clientResults.textContent = 'Buscando…';
      try {
        const response = await fetch(`/painel/clientes/busca?q=${encodeURIComponent(query)}`, {
          headers: { Accept: 'application/json' },
        });
        if (!response.ok) throw new Error('Falha na busca');
        const data = await response.json();
        if (currentRequest !== requestNumber) return;
        clientResults.replaceChildren();
        if (!data.clients.length) {
          clientResults.textContent = 'Nenhum cliente encontrado.';
          return;
        }
        data.clients.forEach((client) => {
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'client-result';
          const name = document.createElement('b');
          name.textContent = client.name;
          const details = document.createElement('span');
          details.textContent = `${client.public_id} · ${client.phone}`;
          button.append(name, details);
          button.addEventListener('click', () => selectClient(client.id, client.name, client.public_id));
          clientResults.appendChild(button);
        });
      } catch (_error) {
        clientResults.textContent = 'Não foi possível buscar. Tente novamente.';
      }
    }, 250);
  });
}

document.querySelector('#package-form')?.addEventListener('submit', (event) => {
  if (!clientId.value) {
    event.preventDefault();
    alert('Busque e selecione um cliente válido antes de registrar o pacote.');
    clientSearch.focus();
  }
});

const registrationForm = document.querySelector('#registration-form');
if (registrationForm) {
  const storageKey = 'adelinha-registration-draft';
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) || '{}');
    ['name', 'cpf', 'phone', 'email', 'residential_cep', 'residential_street', 'residential_number',
      'residential_district', 'residential_city', 'residential_state', 'residential_complement'].forEach((name) => {
      if (saved[name]) registrationForm.elements[name].value = saved[name];
    });
    registrationForm.elements.terms_accept.checked = Boolean(saved.terms_accept)
      && saved.terms_version === registrationForm.elements.terms_version.value
      && saved.terms_digest === registrationForm.elements.terms_digest.value;
    registrationForm.restoreSignature?.(saved.signature);
  } catch (_error) { /* armazenamento indisponível não impede o cadastro */ }
  const saveRegistrationDraft = () => {
    try { sessionStorage.setItem(storageKey, JSON.stringify({
      name: registrationForm.elements.name.value,
      cpf: registrationForm.elements.cpf.value,
      phone: registrationForm.elements.phone.value,
      email: registrationForm.elements.email.value,
      residential_cep: registrationForm.elements.residential_cep.value,
      residential_street: registrationForm.elements.residential_street.value,
      residential_number: registrationForm.elements.residential_number.value,
      residential_district: registrationForm.elements.residential_district.value,
      residential_city: registrationForm.elements.residential_city.value,
      residential_state: registrationForm.elements.residential_state.value,
      residential_complement: registrationForm.elements.residential_complement.value,
      terms_version: registrationForm.elements.terms_version.value,
      terms_digest: registrationForm.elements.terms_digest.value,
      terms_accept: registrationForm.elements.terms_accept.checked,
      signature: registrationForm.querySelector('input[name="signature"]').value,
    })); } catch (_) { /* Documentos abrem em outra aba; o formulário original permanece intacto. */ }
  };
  document.querySelectorAll('.terms-link, [data-registration-document]').forEach(link => link.addEventListener('click', saveRegistrationDraft));
  registrationForm.addEventListener('submit', () => sessionStorage.removeItem(storageKey));
}

const documentType = document.querySelector('#document-type');
const documentNumber = document.querySelector('#document-number');
const documentLabel = document.querySelector('#document-number-label');
documentType?.addEventListener('change', () => {
  documentNumber.value = '';
  documentNumber.required = Boolean(documentType.value);
  documentNumber.placeholder = documentType.value === 'CPF' ? '000.000.000-00' : documentType.value === 'CNH' ? '11 dígitos' : 'Número do RG';
  documentNumber.inputMode = documentType.value === 'RG' ? 'text' : 'numeric';
  documentNumber.pattern = documentType.value === 'CPF' ? '[0-9.\\-]{11,14}' : documentType.value === 'CNH' ? '[0-9]{11}' : '[A-Za-z0-9.\\-]{5,20}';
  documentLabel.hidden = !documentType.value;
});
documentNumber?.addEventListener('input', () => {
  if (documentType.value === 'CPF') {
    const value = documentNumber.value.replace(/\D/g, '').slice(0, 11);
    documentNumber.value = value.replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d)/, '$1.$2').replace(/(\d{3})(\d{1,2})$/, '$1-$2');
  } else if (documentType.value === 'CNH') {
    documentNumber.value = documentNumber.value.replace(/\D/g, '').slice(0, 11);
  } else {
    documentNumber.value = documentNumber.value.toUpperCase().replace(/[^A-Z0-9.\-]/g, '').slice(0, 20);
  }
});

const pickupForm = document.querySelector('#pickup-form');
const paymentBox = document.querySelector('#payment-summary');
const pixArea = document.querySelector('#pix-area');
const confirmation = document.querySelector('#payment-confirmed');
const submitPickup = document.querySelector('#pickup-submit');
const discountAmount = document.querySelector('#discount-amount');
const discountPercent = document.querySelector('#discount-percent');
const surchargeAmount = document.querySelector('#surcharge-amount');
const surchargeReason = document.querySelector('#surcharge-reason');
let paymentRequest = 0;
async function updatePayment() {
  if (!pickupForm) return;
  const currentRequest = ++paymentRequest;
  confirmation.checked = false;
  const method = pickupForm.querySelector('[name="payment_method"]:checked')?.value;
  const ids = [...document.querySelectorAll('[name="package_ids"]:checked')];
  paymentBox.hidden = !method || !ids.length;
  submitPickup.textContent = 'Confirmar retirada';
  const fastArea=document.querySelector('#pickup-fast-area');
  if(fastArea)fastArea.hidden=method!=='Pix Fast';
  submitPickup.disabled=method==='Pix Fast';
  document.querySelector('#payment-confirmation-text').textContent = method === 'Pix AI'
    ? 'Conferi o pagamento Pix na conta e confirmo o recebimento.'
    : 'Conferi o recebimento em dinheiro.';
  pixArea.hidden = method !== 'Pix AI';
  if (!method || !ids.length) return;
  const body = new FormData();
  ids.forEach((checkbox) => body.append('package_ids', checkbox.value));
  body.append('payment_method',method);
  body.append('discount_amount', discountAmount?.value || '');
  body.append('discount_percent', discountPercent?.value || '');
  body.append('surcharge_amount', surchargeAmount?.value || '0');
  body.append('surcharge_reason', surchargeReason?.value || '');
  body.append('csrf_token', document.querySelector('meta[name="csrf-token"]').content);
  const response = await fetch('/painel/retirada/resumo', { method: 'POST', body });
  const data = await response.json();
  if (currentRequest !== paymentRequest) return;
  const error=document.querySelector('#pickup-payment-error');error.hidden=response.ok;error.textContent=data.error||'';
  const link=document.querySelector('#existing-pickup-fast');link.hidden=!data.payment_url;if(data.payment_url)link.href=data.payment_url;
  if (!response.ok) {pixArea.hidden=true;submitPickup.disabled=true;if(data.payment_url)fastArea.hidden=false;return;}

  document.querySelector('#pickup-package-total').textContent = data.package_total_display;
  document.querySelector('#pickup-discount').textContent = data.discount_display;
  document.querySelector('#pickup-surcharge').textContent = data.surcharge_display;
  document.querySelector('#pickup-total').textContent = data.total_display;
  if(method==='Pix Fast'){
    document.querySelector('#payment-confirmation-text').textContent=data.pix_zero_total?'Total zero: confiro a entrega sem cobrança.':'A entrega será confirmada na tela do Pix Fast após o pagamento.';
    submitPickup.disabled=!data.pix_zero_total;
    document.querySelector('#generate-pickup-fast').disabled=data.pix_zero_total;
    document.querySelector('#pickup-fast-info').textContent=data.pix_zero_total?'Total zero: confirme a retirada sem gerar cobrança.':'Pagamento deve ser confirmado pela API antes da entrega.';
  }
  if (method === 'Pix AI') {
    if(data.pix_zero_total){pixArea.hidden=true;submitPickup.disabled=false;document.querySelector('#pix-code').value='';document.querySelector('#pix-qr').removeAttribute('src');document.querySelector('#payment-confirmation-text').textContent='Total zero: confiro a entrega sem cobrança.';}
    else if (!data.pix_configured) {
      pixArea.hidden=true;submitPickup.disabled=!data.pix_zero_total;error.hidden=false;error.textContent='Configure a chave CNPJ e o nome do titular para usar Pix AI.';
    } else if (data.pix_zero_total) {
      document.querySelector('#pix-qr').removeAttribute('src');
      document.querySelector('#pix-code').value = '';
      document.querySelector('#pix-code').placeholder = 'Total zerado: não é necessário gerar cobrança Pix.';
    } else {
      document.querySelector('#pix-qr').src = data.qr_code;
      document.querySelector('#pix-code').value = data.pix_code;
      document.querySelector('#pix-code').placeholder = '';
    }
  }
}
document.querySelector('#generate-pickup-fast')?.addEventListener('click',async(event)=>{
  const button=event.currentTarget;button.disabled=true;
  const old=document.querySelector('#existing-pickup-fast');if(old&&!old.hidden){location.assign(old.href);return;}
  const body=new FormData(pickupForm);body.delete('package_ids');
  document.querySelectorAll('[name="package_ids"]:checked').forEach(e=>body.append('package_ids',e.value));
  try{const response=await fetch('/painel/retirada/pix-fast',{method:'POST',body});const data=await response.json();
    if(!response.ok){alert(data.error);return;}location.assign(data.url);
  }catch(_){alert('Criação em consulta. Consulte a solicitação existente antes de tentar novamente.');}
  finally{button.disabled=false;}
});
document.querySelectorAll('[name="package_ids"], [name="payment_method"]').forEach((input) => input.addEventListener('change', updatePayment));
let discountTimer;
[[discountAmount, discountPercent], [discountPercent, discountAmount]].forEach(([field, other]) => {
  field?.addEventListener('input', () => {
    if (field.value) other.value = '';
    confirmation.checked = false;
    clearTimeout(discountTimer);
    discountTimer = setTimeout(updatePayment, 180);
  });
});
[surchargeAmount, surchargeReason].forEach((field) => field?.addEventListener('input', () => {
  const amount = Number((surchargeAmount.value || '0').replace(',', '.'));
  surchargeReason.required = Number.isFinite(amount) && amount > 0;
  confirmation.checked = false;
  clearTimeout(discountTimer);
  discountTimer = setTimeout(updatePayment, 180);
}));

if (pickupForm) {
  const key = 'adelinha-pickup-draft';
  try {
    const draft = JSON.parse(sessionStorage.getItem(key) || '{}');
    ['receiver_name', 'document_type', 'receiver_document'].forEach((name) => {
      if (draft[name]) pickupForm.elements[name].value = draft[name];
    });
    if (draft.document_type) documentType.dispatchEvent(new Event('change'));
    if (draft.receiver_document) documentNumber.value = draft.receiver_document;
    if (draft.payment_method) {
      const method = pickupForm.querySelector(`[name="payment_method"][value="${draft.payment_method}"]`);
      if (method) method.checked = true;
    }
    discountAmount.value = draft.discount_amount || '';
    discountPercent.value = draft.discount_percent || '';
    surchargeAmount.value = draft.surcharge_amount || '0,00';
    surchargeReason.value = draft.surcharge_reason || '';
    surchargeAmount.dispatchEvent(new Event('input'));
    (draft.package_ids || []).forEach((id) => {
      const field = document.querySelector(`[name="package_ids"][value="${id}"]`);
      if (field) field.checked = true;
    });
    pickupForm.restoreSignature?.(draft.signature);
    updatePayment();
  } catch (_error) { /* rascunho inválido é ignorado */ }
  pickupForm.addEventListener('submit', () => {
    sessionStorage.setItem(key, JSON.stringify({
      receiver_name: pickupForm.elements.receiver_name.value,
      document_type: pickupForm.elements.document_type.value,
      receiver_document: pickupForm.elements.receiver_document.value,
      payment_method: pickupForm.querySelector('[name="payment_method"]:checked')?.value,
      discount_amount: discountAmount.value,
      discount_percent: discountPercent.value,
      surcharge_amount: surchargeAmount.value,
      surcharge_reason: surchargeReason.value,
      package_ids: [...document.querySelectorAll('[name="package_ids"]:checked')].map((item) => item.value),
      signature: pickupForm.querySelector('.signature-value').value,
    }));
  });
}
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/static/sw.js');
let reconnectNotice;
window.addEventListener('offline',()=>{reconnectNotice=document.createElement('div');reconnectNotice.className='flash warning reconnect';reconnectNotice.textContent='Reconectando ao sistema…';document.body.prepend(reconnectNotice)});
window.addEventListener('online',()=>{if(reconnectNotice)reconnectNotice.remove()});
window.addEventListener('beforeunload',()=>sessionStorage.setItem('adelinha-scroll',String(scrollY)));
window.addEventListener('pageshow',()=>{if(location.hash)return;const y=Number(sessionStorage.getItem('adelinha-scroll'));if(y)scrollTo(0,y)});
document.querySelector('#send-registration-code')?.addEventListener('click',async()=>{const form=document.querySelector('#registration-form'),status=document.querySelector('#registration-code-status');status.textContent='Enviando código…';try{const response=await fetch('/cadastro/enviar-codigo',{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'fetch'}});const data=await response.json();status.textContent=data.message||data.error}catch(_){status.textContent='Não foi possível enviar. Confira a conexão e tente novamente.'}});

document.querySelectorAll('[data-carousel]').forEach((carousel) => {
  const slides = [...carousel.querySelectorAll('[data-slide]')];
  if (slides.length < 2) return;
  const dots = [...carousel.querySelectorAll('[data-slide-to]')];
  const track = carousel.querySelector('.banner-slides');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const mouseDevice = matchMedia('(hover: hover) and (pointer: fine)');
  const interval = 3000;
  let current = Math.max(0, slides.findIndex(slide => slide.classList.contains('active')));
  let timer = null;
  let generation = 0;
  let hovered = false;
  let touching = false;
  let touchStart = null;
  let suspended = document.hidden || !document.hasFocus();
  const focused = () => {
    const active = document.activeElement;
    // A tap may leave a dot focused indefinitely. Only desktop control focus
    // or visible keyboard focus should keep autoplay paused after touchend.
    return dots.includes(active) && (mouseDevice.matches || active.matches(':focus-visible'));
  };
  const paused = () => suspended || document.hidden || reducedMotion.matches || hovered || touching || focused();
  const show = (index) => {
    current = (index + slides.length) % slides.length;
    slides.forEach((slide, i) => {
      slide.classList.toggle('active', i === current);
      slide.setAttribute('aria-hidden', String(i !== current));
    });
    dots.forEach((dot, i) => i === current ? dot.setAttribute('aria-current', 'true') : dot.removeAttribute('aria-current'));
  };
  const schedule = () => {
    clearTimeout(timer);
    timer = null;
    const token = ++generation;
    const stopped = paused();
    track.setAttribute('aria-live', stopped ? 'polite' : 'off');
    if (stopped) return;
    const started = performance.now();
    const wallStarted = Date.now();
    timer = setTimeout(() => {
      if (token !== generation) return;
      timer = null;
      if (paused()) { schedule(); return; }
      // Some OS/browser sleep paths do not deliver lifecycle events. Never
      // replay an overdue tick after suspension: wait a fresh full interval.
      const elapsed = Math.max(performance.now() - started, Date.now() - wallStarted);
      if (elapsed > interval + 250) { schedule(); return; }
      show(current + 1);
      schedule();
    }, interval);
  };
  const navigate = (index) => { show(index); schedule(); };
  const suspend = () => {
    suspended = true;
    hovered = false;
    touching = false;
    touchStart = null;
    schedule();
  };
  const resume = () => {
    suspended = document.hidden || !document.hasFocus();
    touching = false;
    touchStart = null;
    hovered = !suspended && mouseDevice.matches && carousel.matches(':hover');
    schedule();
  };
  dots.forEach((dot, index) => dot.addEventListener('click', () => navigate(index)));
  carousel.addEventListener('pointerenter', event => {
    if (event.pointerType === 'mouse' && !hovered) { hovered = true; schedule(); }
  });
  carousel.addEventListener('pointerleave', event => {
    if (event.pointerType === 'mouse' && hovered) { hovered = false; schedule(); }
  });
  // Reconcile hover even if pointerleave was lost during tab/OS suspension.
  document.addEventListener('pointermove', event => {
    if (event.pointerType !== 'mouse' || suspended) return;
    const inside = carousel.contains(event.target);
    if (inside !== hovered) { hovered = inside; schedule(); }
  }, {passive: true});
  carousel.addEventListener('focusin', schedule);
  carousel.addEventListener('focusout', () => queueMicrotask(schedule));
  reducedMotion.addEventListener('change', schedule);
  mouseDevice.addEventListener('change', () => {
    hovered = !suspended && mouseDevice.matches && carousel.matches(':hover');
    schedule();
  });
  carousel.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault(); navigate(current + (event.key === 'ArrowRight' ? 1 : -1));
    }
  });
  carousel.addEventListener('touchstart', event => {
    touching = true;
    touchStart = event.touches.length === 1 && track.contains(event.target)
      ? {x: event.touches[0].clientX, y: event.touches[0].clientY} : null;
    schedule();
  }, {passive: true});
  window.addEventListener('touchend', event => {
    if (!touching) return;
    if (event.touches.length) { touchStart = null; return; }
    touching = false;
    const start = touchStart;
    touchStart = null;
    if (start && event.changedTouches.length) {
      const dx = event.changedTouches[0].clientX - start.x;
      const dy = event.changedTouches[0].clientY - start.y;
      if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy)) {
        navigate(current + (dx < 0 ? 1 : -1));
        return;
      }
    }
    schedule();
  }, {passive: true});
  window.addEventListener('touchcancel', () => {
    if (!touching) return;
    touching = false; touchStart = null; schedule();
  }, {passive: true});
  document.addEventListener('visibilitychange', () => document.hidden ? suspend() : resume());
  document.addEventListener('freeze', suspend);
  document.addEventListener('resume', resume);
  window.addEventListener('blur', suspend);
  window.addEventListener('focus', resume);
  window.addEventListener('pagehide', suspend);
  window.addEventListener('pageshow', resume);
  show(current);
  resume();
});

const themeForm = document.querySelector('#theme-form');
if (themeForm) {
  const preview = themeForm.querySelector('.theme-preview');
  const updatePreview = () => {
    const f = themeForm.elements;
    preview.style.background = f.page.value; preview.style.color = f.text.value; preview.style.borderColor = f.border.value;
    preview.querySelector('a').style.color = f.link.value; preview.querySelector('button').style.background = f.button.value;
    const luminance = (color) => {
      const rgb = color.match(/[0-9a-f]{2}/gi).map(hex => parseInt(hex, 16) / 255)
        .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
      return rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
    };
    const background = luminance(f.button.value);
    const graphiteContrast = (Math.max(background, luminance('#262626')) + .05) / (Math.min(background, luminance('#262626')) + .05);
    preview.querySelector('button').style.color = graphiteContrast >= 4.5 ? '#262626' : (1.05 / (background + .05) >= (background + .05) / .05 ? '#ffffff' : '#000000');
  };
  themeForm.querySelectorAll('input[type=color]').forEach((field) => field.addEventListener('input', updatePreview)); updatePreview();
}

document.querySelectorAll('[data-image-upload], [data-banner-upload]').forEach((input) => {
  const maxBytes = Number(input.dataset.maxBytes) || 900 * 1024;
  const form = input.closest('form');
  const preview = form.querySelector('[data-banner-preview]');
  const error = form.querySelector('[data-upload-error]');
  const originalSrc = preview?.getAttribute('src');
  const resetPreview = () => {
    if (!preview) return;
    if (preview.dataset.objectUrl) URL.revokeObjectURL(preview.dataset.objectUrl);
    delete preview.dataset.objectUrl;
    if (originalSrc) { preview.src = originalSrc; preview.hidden = false; }
    else { preview.removeAttribute('src'); preview.hidden = true; }
  };
  const invalid = () => {
    resetPreview(); input.value = '';
    const message = `Imagem inválida. Envie PNG, JPG ou WebP de até ${maxBytes / 1024} KB. A imagem anterior foi preservada.`;
    input.setCustomValidity(message);
    if (error) { error.textContent = message; error.hidden = false; }
    input.reportValidity();
  };
  input.addEventListener('change', async () => {
    input.setCustomValidity('');
    if (error) error.hidden = true;
    const file = input.files[0];
    resetPreview();
    if (!file) return;
    if (file.size > maxBytes || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) { invalid(); return; }
    // Confere a assinatura real e a decodificação, além do MIME fornecido pelo navegador.
    input.setCustomValidity('Aguarde a validação da imagem.');
    const bytes = new Uint8Array(await file.slice(0, 12).arrayBuffer());
    if (input.files[0] !== file) return;
    const png = [137,80,78,71,13,10,26,10].every((b,i) => bytes[i] === b);
    const jpg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    const webp = String.fromCharCode(...bytes.slice(0,4)) === 'RIFF' && String.fromCharCode(...bytes.slice(8,12)) === 'WEBP';
    if (!png && !jpg && !webp) { invalid(); return; }
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      if (input.files[0] !== file) { URL.revokeObjectURL(url); return; }
      input.setCustomValidity('');
      if (preview) { preview.dataset.objectUrl = url; preview.src = url; preview.hidden = false; }
      else URL.revokeObjectURL(url);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      if (input.files[0] === file) invalid();
    };
    image.src = url;
  });
});

const navToggle = document.querySelector('[data-nav-toggle]');
const mainNav = document.querySelector('[data-main-nav]');
const siteHeader = navToggle?.closest('.site-header');
if (siteHeader && mainNav) {
  let navFrame = 0;
  const closeNavigation = (restoreFocus = false) => {
    mainNav.classList.remove('open');
    navToggle.setAttribute('aria-expanded', 'false');
    if (restoreFocus) navToggle.focus();
  };
  const fitNavigation = () => {
    navFrame = 0;
    siteHeader.classList.add('nav-measuring');
    const navWidth = mainNav.getBoundingClientRect().width;
    const brandWidth = siteHeader.querySelector('.brand').getBoundingClientRect().width;
    const style = getComputedStyle(siteHeader);
    const available = siteHeader.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    siteHeader.classList.remove('nav-measuring');
    const compact = matchMedia('(max-width: 47.99rem)').matches || navWidth + brandWidth + parseFloat(style.columnGap) > available;
    siteHeader.classList.toggle('nav-compact', compact);
    siteHeader.classList.toggle('nav-wide', !compact);
    if (!compact) closeNavigation();
  };
  const scheduleNavigation = () => { if (!navFrame) navFrame = requestAnimationFrame(fitNavigation); };
  navToggle.addEventListener('click', () => {
    const open = navToggle.getAttribute('aria-expanded') !== 'true';
    navToggle.setAttribute('aria-expanded', String(open));
    mainNav.classList.toggle('open', open);
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && mainNav.classList.contains('open')) closeNavigation(true);
  });
  document.addEventListener('click', event => {
    if (!siteHeader.contains(event.target)) closeNavigation();
  });
  window.addEventListener('resize', scheduleNavigation);
  new ResizeObserver(scheduleNavigation).observe(siteHeader);
  document.fonts.ready.then(scheduleNavigation);
  siteHeader.querySelector('img')?.addEventListener('load', scheduleNavigation);
  fitNavigation();
}

document.querySelectorAll('[data-metric]').forEach((link) => {
  link.addEventListener('click', () => {
    const data = new FormData();
    data.set('event', link.dataset.metric);
    data.set('path', location.pathname);
    data.set('csrf_token', document.querySelector('meta[name="csrf-token"]')?.content || '');
    fetch('/metricas/evento', {method: 'POST', body: data, credentials: 'same-origin', keepalive: true}).catch(() => {});
  });
});

// O suporte ocupa apenas espaço livre; não encobre controles, textos ou imagens.
const supportFloat = document.querySelector('.support-float');
if (supportFloat) {
  let supportFrame = 0;
  let textNodes = [];
  const refreshSupportText = () => {
    textNodes = [];
    document.querySelectorAll('.site-header, main, footer').forEach(root => {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const node = walker.currentNode;
        if (node.textContent.trim() && !node.parentElement.closest('script,style,select,textarea,[hidden]')) textNodes.push(node);
      }
    });
  };
  const updateSupportPosition = () => {
    supportFrame = 0;
    const support = supportFloat.getBoundingClientRect();
    const intersects = box => box.width > 0 && box.height > 0 && box.left < support.right && box.right > support.left && box.top < support.bottom && box.bottom > support.top;
    const keyboard = matchMedia('(max-width: 64rem)').matches && document.activeElement?.matches('input:not([type="hidden"]),textarea,select');
    const controls = document.querySelectorAll('.site-header a,.site-header button,.site-header .logged-user,main input:not([type="hidden"]),main textarea,main select,main button,main a.button,main summary,main canvas,main img,footer a');
    let overlaps = keyboard || Array.from(controls).some(control => !control.closest('[hidden],[aria-hidden="true"]') && intersects(control.getBoundingClientRect()));
    if (!overlaps) {
      const range = document.createRange();
      overlaps = textNodes.some(node => {
        if (!node.isConnected || node.parentElement.closest('[hidden],[aria-hidden="true"]')) return false;
        range.selectNodeContents(node);
        return Array.from(range.getClientRects()).some(intersects);
      });
    }
    supportFloat.toggleAttribute('data-control-overlap', Boolean(overlaps) && document.activeElement !== supportFloat);
  };
  const scheduleSupportPosition = () => {
    if (!supportFrame) supportFrame = requestAnimationFrame(updateSupportPosition);
  };
  window.addEventListener('scroll', scheduleSupportPosition, {passive: true});
  window.addEventListener('resize', scheduleSupportPosition);
  document.addEventListener('toggle', scheduleSupportPosition, true);
  document.addEventListener('focusin', scheduleSupportPosition);
  document.addEventListener('focusout', scheduleSupportPosition);
  new ResizeObserver(scheduleSupportPosition).observe(document.body);
  const content = document.querySelector('main');
  if (content) new MutationObserver(() => { refreshSupportText(); scheduleSupportPosition(); }).observe(content, {subtree: true, childList: true, characterData: true});
  refreshSupportText();
  scheduleSupportPosition();
}

// Native dialog provides modal focus confinement and Escape handling.
const cardDialog = document.querySelector('[data-card-dialog]');
if (cardDialog && typeof cardDialog.showModal === 'function') {
  const full = cardDialog.querySelector('[data-card-full]');
  const error = cardDialog.querySelector('[data-card-load-error]');
  let opener;
  document.querySelectorAll('[data-card-enlarge]').forEach((link) => {
    link.addEventListener('click', (event) => {
      if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault(); opener = link;
      cardDialog.querySelector('#card-dialog-title').textContent = link.dataset.title;
      full.alt = link.dataset.alt;
      error.hidden = true;
      full.src = link.href; // Original is never requested before this interaction.
      cardDialog.showModal();
      cardDialog.querySelector('[data-card-close]').focus();
    });
  });
  full.addEventListener('error', () => { error.hidden = false; });
  cardDialog.querySelector('[data-card-close]').addEventListener('click', () => cardDialog.close());
  cardDialog.addEventListener('click', (event) => {
    if (!event.target.closest('img,button')) cardDialog.close();
  });
  cardDialog.addEventListener('keydown', (event) => {
    // With a single control, keep Tab/Shift+Tab on the close button instead
    // of allowing browser chrome to take focus during the modal cycle.
    if (event.key === 'Tab') {
      event.preventDefault();
      cardDialog.querySelector('[data-card-close]').focus();
    }
  });
  cardDialog.addEventListener('close', () => {
    full.removeAttribute('src');
    opener?.focus({preventScroll:true});
  });
}

// Requesting another email code must not clear a signature already drawn.
document.querySelectorAll('[data-regularization-email], [data-email-in-place]').forEach(form => {
  // Enter in the received code saves the form rather than sending another code.
  form.addEventListener('keydown', event => {
    if (event.key === 'Enter' && event.target.name === 'code' && form.hasAttribute('data-email-in-place')) {
      event.preventDefault();
      form.requestSubmit(form.querySelector('button.primary[type=submit]'));
    }
  });
  form.addEventListener('submit', async event => {
    if (form.hasAttribute('data-email-in-place') && !event.submitter?.matches('[data-send-email]')) return;
    event.preventDefault();
    const button = event.submitter || form.querySelector('button');
    const status = form.querySelector('[data-email-status]');
    if (button.disabled) return;
    button.disabled = true;
    status.className = 'email-status';
    status.textContent = 'Enviando código…';
    const body = new FormData(form);
    if (form.hasAttribute('data-email-in-place')) {
      body.set(form.dataset.emailField, 'send_code');
      // Email delivery does not submit an acceptance or store the drawn signature.
      ['signature', 'confirmed', 'terms_accept', 'code', 'email_intent'].forEach(key => body.delete(key));
    }
    try {
      const response = await fetch(form.getAttribute('action') || location.href, {method:'POST',body,credentials:'same-origin',headers:{'X-Requested-With':'XMLHttpRequest','Accept':'application/json'}});
      const result = response.headers.get('content-type')?.includes('application/json') ? await response.json() : null;
      const sent = response.ok && result?.sent === true;
      status.textContent = result?.message || 'Não foi possível enviar o código. Confira sua sessão e tente novamente.';
      status.classList.add(sent ? 'success' : 'error');
    } catch (_) {
      status.textContent = 'Não foi possível enviar o código. Confira a conexão e tente novamente.';
      status.classList.add('error');
    } finally { button.disabled = false; }
  });
});
