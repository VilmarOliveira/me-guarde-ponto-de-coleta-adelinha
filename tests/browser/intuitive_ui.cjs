/* Real-browser checks against synthetic data; outside network calls are blocked. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const BASE = process.env.UI_BASE || 'http://127.0.0.1:5095';
const out = process.env.UI_OUTPUT || '/tmp/intuitive-ui-results';
fs.mkdirSync(out, {recursive:true});
(async () => {
  const browser = await chromium.launch({headless:true});
  const results = [];
  try {
    for (const width of [360,768,1280]) {
      const context = await browser.newContext({viewport:{width,height:900}});
      await context.route('**/*', route => {
        const url = route.request().url();
        return url.startsWith(BASE) || url.startsWith('data:') ? route.continue() : route.abort();
      });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(String(error)));
      async function visit(path, name) {
        const response = await page.goto(BASE+path);
        assert.equal(response.status(),200,path);
        await page.waitForLoadState('networkidle');
        assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'horizontal overflow: '+path);
        for (const link of await page.locator('main a.button').all()) {
          if (/^voltar/i.test(await link.textContent())) assert.ok(await link.locator(':scope > svg').count()<=1);
        }
        if (name) await page.screenshot({path:`${out}/${name}-${width}.png`,fullPage:true});
      }
      await visit('/__visual__/public','home');
      assert.equal(await page.locator('.login-mobile').textContent().then(t=>t.trim()),'Login');
      const sizes = await page.locator('.single-package-prices article > svg').evaluateAll(els=>els.map(e=>e.getBoundingClientRect().width));
      assert.ok(sizes[1]>sizes[0]);
      await visit('/cliente/login','login');
      assert.equal(await page.getByRole('button',{name:'Entrar',exact:true}).locator('svg[data-icon="login"]').count(),1);
      await visit('/__visual__/customer/visao-geral','customer-overview');
      assert.equal(await page.getByRole('heading',{name:'Avisos de retirada'}).locator('svg[data-icon="parcel-notice"]').count(),1);
      await visit('/__visual__/customer/pacotes','customer-packages');
      assert.equal(await page.getByRole('button',{name:'Pagar antes da retirada',exact:true}).locator('svg[data-icon="wallet-clock"]').count(),1);
      await visit('/__visual__/customer/dados','customer-data');
      assert.equal(await page.getByRole('button',{name:'Alterar senha',exact:true}).locator('svg[data-icon="password-change"]').count(),1);
      assert.equal(await page.getByRole('link',{name:'Desvincular conta Google',exact:true}).locator('svg[data-icon="google-unlink"]').count(),1);
      assert.equal(await page.getByRole('heading',{name:'Dados para entrega',exact:true}).locator('svg[data-icon="address-card"]').count(),1);
      await visit('/__visual__/customer/contratos','contracts');
      const contractButtons=page.locator('.contract-actions > a');
      assert.equal(await contractButtons.first().locator('svg[data-icon="contract"]').count(),1);
      if(width>600){const a=await contractButtons.nth(0).boundingBox(),b=await contractButtons.nth(1).boundingBox();assert.ok(Math.abs(a.y-b.y)<2);assert.ok(Math.abs(a.height-b.height)<2);}
      await visit('/cliente/plano','customer-plan');
      assert.equal(await page.locator('.plan-payment-label svg[data-icon="pix"]').count(),1);
      await visit('/__visual__/staff','staff-home');
      const shortcuts=await page.locator('.dashboard-shortcuts').boundingBox();
      assert.ok(Math.abs(shortcuts.x+shortcuts.width/2-width/2)<2);
      const methods = await page.locator('[name=payment_method]').evaluateAll(els=>els.map(e=>e.value));
      assert.deepEqual(methods,['Pix Fast','Pix AI','Dinheiro']);
      await visit('/painel/configuracoes','settings');
      assert.equal(await page.locator('.settings-card').filter({hasText:'Envio de códigos'}).locator('svg[data-icon="mail-code"]').count(),1);

      await visit('/painel/retiradas/historico','history');
      assert.equal(await page.getByLabel('Data específica').count(),0);
      await visit('/painel/configuracoes/pagamentos-cliente','payment-order');
      if(width>767) {
        const first=await page.locator('.payment-order-table tbody tr').first().locator('td').first().boundingBox();
        assert.ok(first.width<130,'order column too wide: '+first.width);
      }
      assert.equal(await page.getByRole('link',{name:'Contratação do plano',exact:true}).locator('svg[data-icon="contract-add"]').count(),1);
      assert.equal(await page.getByRole('link',{name:'Renovação do plano',exact:true}).locator('svg[data-icon="refresh"]').count(),1);
      await visit('/painel/configuracoes/identidade','identity');
      assert.equal(await page.getByRole('link',{name:'Aparência',exact:true}).locator('svg[data-icon="palette"]').count(),1);
      assert.equal(await page.getByRole('link',{name:'Gerenciar banners',exact:true}).count(),0);
      await visit('/painel/blog','blog-editor');
      assert.equal(await page.locator('#blog-compose').getAttribute('open'),null);
      await page.getByRole('link',{name:'Novo artigo',exact:true}).click();
      assert.equal(await page.locator('#blog-compose').evaluate(e=>e.open),true);
      await page.getByLabel('Título',{exact:true}).fill('Artigo visual '+width);
      await page.getByLabel('Introdução / resumo',{exact:true}).fill('Resumo fictício para a validação do editor.');
      await page.getByLabel('Corpo do artigo',{exact:true}).fill('## Como receber\n\nConteúdo fictício de verificação.');
      await page.getByLabel('Endereço do artigo',{exact:true}).fill('visual-'+width);
      await page.getByRole('button',{name:'Pré-visualizar',exact:true}).click();
      await page.locator('[data-blog-dialog]').waitFor({state:'visible'});
      await page.getByRole('button',{name:'Fechar prévia'}).click();
      await page.getByRole('button',{name:'Salvar rascunho',exact:true}).click();
      await page.waitForLoadState('networkidle');
      assert.ok((await page.textContent('main')).includes('Artigo salvo com sucesso!'));
      await visit('/blog','public-blog');
      const article=page.locator('.blog-card h2 a').first();
      if(await article.count()) {await article.click();await page.waitForLoadState('networkidle');await page.screenshot({path:`${out}/article-${width}.png`,fullPage:true});}
      await visit('/painel/configuracoes/plano','plan-admin');
      for (const title of ['Validar participação de clientes','Ciclos e créditos']) {
        const rect=await page.locator('main > details').filter({has:page.locator('summary').filter({hasText:title})}).boundingBox();
        assert.ok(Math.abs(rect.x+rect.width/2-width/2)<2,title+' not centered');
      }
      await page.getByRole('button',{name:'Arquivar',exact:true}).first().click();
      await page.waitForLoadState('networkidle');
      await page.getByRole('link',{name:'Arquivadas',exact:true}).click();
      await page.getByRole('button',{name:'Restaurar',exact:true}).first().click();
      await page.waitForLoadState('networkidle');
      await page.getByRole('link',{name:'Ativas',exact:true}).click();
      assert.ok(await page.getByRole('button',{name:'Arquivar',exact:true}).count());
      assert.deepEqual(errors,[]);
      results.push({width,methods,approvedIcons:true,archiveRestore:true,blogDraft:true,errors});
      await context.close();
    }
    fs.writeFileSync(`${out}/results.json`,JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
