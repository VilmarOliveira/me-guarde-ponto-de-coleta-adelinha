const {chromium}=require('playwright');const assert=require('node:assert/strict');const fs=require('node:fs');
const BASE='http://127.0.0.1:5002';const results=[];fs.mkdirSync('/tmp/adelinha-compliance-results',{recursive:true});
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
try{for(const width of [320,390,768,1440])for(const scenario of ['active','legacy','profile','signature','confirmation','inactive']){
 const context=await browser.newContext({viewport:{width,height:900},isMobile:width<768,hasTouch:width<768});const page=await context.newPage();
 await context.route('https://accounts.google.com/**',async route=>{const u=new URL(route.request().url());await route.fulfill({contentType:'text/html',body:`<a href="${BASE}/cliente/google/retorno?state=${encodeURIComponent(u.searchParams.get('state'))}&code=${scenario}">Concluir Google simulado</a>`});});
 await page.goto(BASE+'/cliente/login');let csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');
 await page.request.post(BASE+'/__google/config',{form:{enabled:'1',csrf_token:csrf}});
 await page.request.post(BASE+'/__google/reset-compliance',{form:{scenario,csrf_token:csrf}});
 await page.locator('[name=cpf]').fill(scenario==='active'?'cliente@example.com':scenario+'@example.com');await page.locator('[name=password]').fill('senha-segura');await page.getByRole('button',{name:'Entrar',exact:true}).click();
 if(scenario==='inactive'){
  assert.equal(new URL(page.url()).pathname,'/cliente/login');
  await page.getByRole('button',{name:'Continuar com o Google'}).click();await page.getByRole('link',{name:'Concluir Google simulado'}).click();
  await page.getByText('Este cadastro está inativo.',{exact:false}).waitFor();results.push({width,scenario});await context.close();continue;
 }
 await page.waitForURL('**/cliente');await page.goto(BASE+'/cliente/pendencias');
 assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 if(scenario==='profile'){
  await page.locator('[name=residential_street]').fill('Rua completada pelo cliente');await page.getByRole('button',{name:'Salvar dados',exact:true}).click();
 }
 if(scenario==='signature'||scenario==='confirmation'){
  let signatureBefore;
  if(scenario==='signature'){
   const canvas=page.locator('canvas.signature');await canvas.scrollIntoViewIfNeeded();const rect=await canvas.boundingBox();const x=rect.x+30,y=rect.y+40;
   if(width<768){const cdp=await context.newCDPSession(page);await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+65,y:y+15}]});await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});}
   else{await page.mouse.move(x,y);await page.mouse.down();await page.mouse.move(x+65,y+15,{steps:5});await page.mouse.up();}
   signatureBefore=await page.locator('[name=signature]').inputValue();assert(signatureBefore.startsWith('data:image/png;base64,'));
   await page.setViewportSize({width:width<768?430:1024,height:900});assert.equal(await page.locator('[name=signature]').inputValue(),signatureBefore);await page.setViewportSize({width,height:900});
  }
  await page.getByRole('button',{name:'Enviar código por e-mail'}).click();await page.locator('[data-email-status]').filter({hasText:'Código enviado'}).waitFor();
  if(signatureBefore)assert.equal(await page.locator('[name=signature]').inputValue(),signatureBefore);
  const response=await page.request.get(BASE+'/__google/current-code');const {code}=await response.json();assert(code);
  await page.locator('[name=terms_accept]').check();await page.locator('[name=code]').fill(code);await page.getByRole('button',{name:/concluir regularização/}).click();
 }
 await page.getByText('Seu cadastro está completo e válido',{exact:false}).waitFor();
 await page.screenshot({path:`/tmp/adelinha-compliance-results/${scenario}-${width}.png`});
 csrf=await page.locator('meta[name=csrf-token]').getAttribute('content');await page.request.post(BASE+'/cliente/sair',{form:{csrf_token:csrf}});await page.goto(BASE+'/cliente/login');
 await page.getByRole('button',{name:'Continuar com o Google'}).click();await page.getByRole('link',{name:'Concluir Google simulado'}).click();
 await page.getByRole('heading',{name:'Confirmar vínculo com o Google'}).waitFor();await page.locator('[name=password]').fill('senha-segura');await page.getByRole('button',{name:'Confirmar e entrar'}).click();await page.waitForURL('**/cliente');
 await page.goto(BASE+'/cliente/condicoes-aceitas');if(scenario!=='legacy')assert(await page.getByRole('heading',{name:'Contrato — Versão 1',exact:true}).count());
 await page.goto(BASE+'/painel');assert.equal(new URL(page.url()).pathname,'/login');results.push({width,scenario});await context.close();
}
fs.writeFileSync('/tmp/adelinha-compliance-results/report.json',JSON.stringify({passed:true,results,google:'simulated; cryptographic verification covered by pytest'},null,2));console.log(`PASS ${results.length} compliance scenarios: ownership, profiles, old signatures, email confirmation, append-only acceptance, Google and inactive accounts.`);
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
