const {chromium}=require('playwright');const assert=require('node:assert/strict');const fs=require('node:fs');
const BASE='http://127.0.0.1:5001';const widths=[320,360,390,430,768,1024,1440];const results=[];
fs.mkdirSync('/tmp/adelinha-privacy-results',{recursive:true});
(async()=>{const browser=await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});try{
 for(const width of widths){
  const context=await browser.newContext({viewport:{width,height:900},isMobile:width<768,hasTouch:width<768});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  for(const path of ['/privacidade','/condicoes','/cliente/login','/blog']){
   const response=await page.goto(BASE+path);assert.equal(response.status(),200);
   assert.equal(await page.locator('footer a[href="/privacidade"]').count(),1);assert.equal(await page.locator('footer a[href="/condicoes"]').count(),1);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert(await page.locator('main p').evaluateAll(els=>els.every(el=>parseFloat(getComputedStyle(el).fontSize)>=14)));
   results.push({width,path});
  }
  await page.goto(BASE+'/');const form=page.locator('#registration-form');await form.locator('[name=name]').fill('Cliente preservado nos documentos');await form.locator('[name=cpf]').fill('52998224725');await form.locator('[name=email]').fill('cliente@example.test');await form.locator('[name=phone]').fill('16999999999');
  await form.locator('[name=password]').fill('senha-apenas-no-formulario');await form.locator('[name=password_confirm]').fill('senha-apenas-no-formulario');
  await form.locator('[name=confirmation_code]').fill('123456');await form.locator('[name=terms_accept]').check();
  const canvas=form.locator('canvas');await canvas.scrollIntoViewIfNeeded();const r=await canvas.boundingBox();await page.mouse.move(r.x+30,r.y+30);await page.mouse.down();await page.mouse.move(r.x+90,r.y+45,{steps:4});await page.mouse.up();
  const signature=await form.locator('[name=signature]').inputValue();assert(signature.startsWith('data:image/png;base64,'));
  for(const link of [form.getByRole('link',{name:'Política de Privacidade',exact:true}),page.locator('footer a[href="/privacidade"]'),page.locator('footer a[href="/condicoes"]')]){
   const popupPromise=page.waitForEvent('popup');await link.click();const doc=await popupPromise;await doc.waitForLoadState();
   assert.equal(await doc.evaluate(()=>window.opener===null),true);assert(await doc.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   assert.equal(await form.locator('[name=name]').inputValue(),'Cliente preservado nos documentos');assert.equal(await form.locator('[name=password]').inputValue(),'senha-apenas-no-formulario');assert.equal(await form.locator('[name=confirmation_code]').inputValue(),'123456');assert.equal(await form.locator('[name=signature]').inputValue(),signature);assert(await form.locator('[name=terms_accept]').isChecked());
   if(new URL(doc.url()).pathname==='/privacidade'){assert(await doc.getByRole('heading',{name:'Política de Privacidade',exact:true}).count());assert(await doc.getByText('Minuta para revisão',{exact:false}).count());await doc.screenshot({path:`/tmp/adelinha-privacy-results/privacy-${width}.png`});}
   await doc.close();
  }
  const draft=await page.evaluate(()=>sessionStorage.getItem('adelinha-registration-draft'));assert(draft.includes('Cliente preservado'));assert(!draft.includes('senha-apenas-no-formulario')&&!draft.includes('123456'));
  // Blocked storage must still leave the original form/signature intact.
  await page.evaluate(()=>{Storage.prototype.setItem=function(){throw new Error('storage blocked for test')};});
  const popupPromise=page.waitForEvent('popup');await form.getByRole('link',{name:'Política de Privacidade',exact:true}).click();const doc=await popupPromise;await doc.waitForLoadState();await doc.close();assert.equal(await form.locator('[name=signature]').inputValue(),signature);assert.equal(errors.length,0);
  await context.close();
 }
 fs.writeFileSync('/tmp/adelinha-privacy-results/report.json',JSON.stringify({passed:true,results,documentFlows:28},null,2));console.log(`PASS ${results.length} public page/width checks plus 28 document flows, inputs/signature, blocked storage and no plaintext password/code in draft.`);
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1});
