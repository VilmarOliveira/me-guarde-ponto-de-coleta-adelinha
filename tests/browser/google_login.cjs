const {chromium}=require('playwright');const assert=require('node:assert/strict');const fs=require('node:fs');
fs.mkdirSync('/tmp/adelinha-google-results',{recursive:true});
const BASE='http://127.0.0.1:5002';const widths=[320,390,768,1440];const cases=[];
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{
 for(const width of widths)for(const scenario of ['active','new','incomplete','inactive','cancel','failure']){
  const context=await browser.newContext({viewport:{width,height:900},isMobile:width<768,hasTouch:width<768});const page=await context.newPage();
  await context.route('https://accounts.google.com/**',async route=>{
   const u=new URL(route.request().url());const state=u.searchParams.get('state');assert(u.searchParams.get('code_challenge'));
   const result=scenario==='cancel'?'error=access_denied':'code='+scenario;
   await route.fulfill({contentType:'text/html',body:`<a href="${BASE}/cliente/google/retorno?state=${encodeURIComponent(state)}&${result}">Voltar ao site</a>`});
  });
  await page.goto(BASE+'/cliente/login');
  if(scenario==='active'){
   const csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');
   await page.request.post(BASE+'/__google/reset-link',{form:{csrf_token:csrf}});
  }
  await page.getByRole('button',{name:'Fazer Login com o Google'}).click();await page.getByRole('link',{name:'Voltar ao site'}).click();
  if(scenario==='active'||scenario==='incomplete'){
   await page.getByRole('heading',{name:'Confirmar vínculo com o Google'}).waitFor();
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.locator('[name=password]').fill('senha-segura');await page.getByRole('button',{name:'Confirmar e entrar'}).click();await page.waitForURL('**/cliente');
   assert.equal(await page.locator('.google-signin').count(),0);
   if(scenario==='incomplete'){assert.equal((await page.request.get(BASE+'/cliente/dados-de-entrega')).status(),403);assert.equal((await page.request.get(BASE+'/cliente/pendencias')).status(),200);}
   await page.goto(BASE+'/painel');assert.equal(new URL(page.url()).pathname,'/login');
   const csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');
   await page.request.post(BASE+'/cliente/sair',{form:{csrf_token:csrf}});await page.goto(BASE+'/cliente/login');
   await page.getByRole('button',{name:'Fazer Login com o Google'}).click();await page.getByRole('link',{name:'Voltar ao site'}).click();await page.waitForURL('**/cliente');
  } else if(scenario==='new'){
   await page.waitForURL('**/#cadastro');
   const notice=page.locator('#cadastro [role=alert]');assert((await notice.textContent()).includes('Para entrar com o Google, primeiro faça seu cadastro no site. Utilize o mesmo e-mail da sua conta Google.'));
   await page.waitForFunction(()=>{const r=document.querySelector('#cadastro [role=alert]').getBoundingClientRect();return r.top>=0&&r.bottom<innerHeight});
  }else{
   await page.waitForURL('**/cliente/login');
   assert(await page.getByText({incomplete:'etapas pendentes',inactive:'inativo',cancel:'cancelada',failure:'Não foi possível validar'}[scenario],{exact:false}).count());
  }
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));cases.push({width,scenario});await page.screenshot({path:`/tmp/adelinha-google-results/${scenario}-${width}.png`});await context.close();
 }
 const page=await browser.newPage();await page.goto(BASE+'/cliente/login');let csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');
 await page.request.post(BASE+'/__google/config',{form:{enabled:'0',csrf_token:csrf}});await page.reload();assert.equal(await page.locator('.google-signin').count(),0);
 await page.locator('[name=cpf]').fill('cliente@example.com');await page.locator('[name=password]').fill('senha-segura');await page.getByRole('button',{name:'Entrar',exact:true}).click();await page.waitForURL('**/cliente');
 await page.goto(BASE+'/condicoes');assert(await page.getByText('ADELINHA-SERIE-2026-11-v1',{exact:true}).count());const pdf=await page.request.get(BASE+'/condicoes/pdf');assert.equal(pdf.headers()['content-type'],'application/pdf');assert((await pdf.body()).subarray(0,4).toString()==='%PDF');
 csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');await page.request.post(BASE+'/cliente/sair',{form:{csrf_token:csrf}});
 await page.goto(BASE+'/');
 await page.evaluate(()=>sessionStorage.setItem('adelinha-registration-draft',JSON.stringify({name:'Rascunho anterior',terms_accept:true,terms_version:'CONTRATO-2026-v2',terms_digest:'old'})));
 await page.reload();assert.equal(await page.locator('#registration-form [name=name]').inputValue(),'Rascunho anterior');assert(!(await page.locator('[name=terms_accept]').isChecked()));
 await page.locator('[name=terms_accept]').check();const opened=page.waitForEvent('popup');await page.locator('.terms-link').click();const contractTab=await opened;await contractTab.waitForLoadState();await contractTab.close();await page.reload();
 assert(await page.locator('[name=terms_accept]').isChecked());
 fs.mkdirSync('/tmp/adelinha-google-results',{recursive:true});fs.writeFileSync('/tmp/adelinha-google-results/report.json',JSON.stringify({passed:true,cases,provider:'simulated; signed-token checks in pytest'},null,2));
 console.log(`PASS ${cases.length} desktop/mobile OAuth scenarios, disabled integration, password fallback and contract PDF.`);
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
