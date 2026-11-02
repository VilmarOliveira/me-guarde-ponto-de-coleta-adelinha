(() => {
  const form=document.querySelector('[data-blog-editor]');if(!form)return;
  const body=form.elements.body,library=form.querySelector('[data-blog-icon-list]');
  const known=new Set(JSON.parse(document.getElementById('blog-known-icons').textContent));
  const warning=form.querySelector('[data-blog-warnings]'),counts=form.querySelector('[data-blog-counts]');
  const search=form.querySelector('[data-blog-icon-search]');
  async function jsonReply(response,message){
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error(message);
    try{return await response.json();}catch(_){throw new Error(message);}
  }
  const normalized=value=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  function update(){
    const value=body.value,withoutMarkers=value.replace(/^\[icone:[^\]\r\n]*\][ \t]*/gim,'');
    counts.textContent=`${Array.from(value).length.toLocaleString('pt-BR')} caracteres com espaços (texto do editor) · ${withoutMarkers.trim()?withoutMarkers.trim().split(/\s+/u).length.toLocaleString('pt-BR'):0} palavras (sem marcações de ícones).`;
    const errors=[];
    form.querySelector('.blog-icon-library').hidden=form.elements.layout.value!=='icons';
    form.querySelector('.blog-option').hidden=form.elements.layout.value!=='icons';
    if(form.elements.layout.value==='icons')value.split('\n').forEach((line,index)=>{const match=line.match(/^\[icone:([^\]]*)\][ \t]*(.*)$/i);if(match&&!known.has(match[1].trim().toLowerCase()))errors.push(`Linha ${index+1}: ícone “${match[1]}” não encontrado.`);if(match&&!match[2].trim())errors.push(`Linha ${index+1}: informe o texto do subtítulo.`);});
    warning.textContent=errors.join(' ');warning.hidden=!errors.length;
  }
  body.addEventListener('input',update);form.elements.layout.addEventListener('change',update);update();
  function filter(){let visible=0;library.querySelectorAll('[data-blog-icon-row]').forEach(row=>{row.hidden=!normalized(row.dataset.search).includes(normalized(search.value));if(!row.hidden)visible++;});form.querySelector('[data-blog-icon-empty]').hidden=visible>0;}
  search.addEventListener('input',filter);
  function setIcon(identifier){
    const start=body.selectionStart===0?0:body.value.lastIndexOf('\n',body.selectionStart-1)+1;
    let end=body.value.indexOf('\n',body.selectionStart);if(end<0)end=body.value.length;
    const line=body.value.slice(start,end),match=line.match(/^\[icone:[^\]]*\][ \t]*/i),heading=line.match(/^#{2,3}[ \t]+/);
    if(identifier){body.setRangeText(`[icone:${identifier}] `,start,start+(match?.[0].length||heading?.[0].length||0),'end');}
    else if(match)body.setRangeText('## ',start,start+match[0].length,'end');
    body.focus();body.dispatchEvent(new Event('input',{bubbles:true}));
  }
  form.querySelector('[data-remove-blog-icon]').addEventListener('click',()=>setIcon(null));
  const csrf=()=>document.querySelector('meta[name=csrf-token]').content;
  library.addEventListener('click',async event=>{
    const insert=event.target.closest('[data-blog-icon]');if(insert){setIcon(insert.dataset.blogIcon);return;}
    const archive=event.target.closest('[data-archive-icon]');if(!archive)return;
    if(!confirm('Remover da biblioteca? As imagens usadas nos artigos serão preservadas.'))return;
    archive.disabled=true;
    try{const data=new FormData();data.set('csrf_token',csrf());const response=await fetch(archive.dataset.archiveIcon,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'}});const result=await jsonReply(response,'Não foi possível remover o ícone. Confira sua sessão e tente novamente.');if(!response.ok)throw new Error(result.message||'Não foi possível remover o ícone.');archive.closest('[data-blog-icon-row]').remove();filter();form.querySelector('[data-blog-preview-status]').textContent=result.message;}
    catch(error){form.querySelector('[data-blog-preview-status]').textContent=error.message;archive.disabled=false;}
  });
  const preview=form.querySelector('[data-blog-preview]'),dialog=document.querySelector('[data-blog-dialog]');
  let previewController;
  preview.addEventListener('click',async()=>{
    previewController?.abort();previewController=new AbortController();preview.disabled=true;
    const status=form.querySelector('[data-blog-preview-status]');status.textContent='Preparando prévia…';
    try{const response=await fetch(form.dataset.previewUrl,{method:'POST',body:new FormData(form),signal:previewController.signal,headers:{'Accept':'application/json'}});const result=await jsonReply(response,'Não foi possível gerar a prévia. Confira sua sessão e tente novamente.');if(!response.ok)throw new Error(result.message||'Não foi possível gerar a prévia.');document.querySelector('[data-blog-preview-content]').innerHTML=result.html;status.textContent=result.warnings.join(' ');if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');}
    catch(error){if(error.name!=='AbortError')status.textContent=error.message;}
    finally{preview.disabled=false;}
  });
  function closePreview(){if(typeof dialog.close==='function')dialog.close();else dialog.removeAttribute('open');preview.focus();}
  dialog.querySelector('[data-close-blog-preview]').addEventListener('click',closePreview);
  dialog.addEventListener('cancel',event=>{event.preventDefault();closePreview();});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closePreview();}});
  dialog.addEventListener('close',()=>preview.focus());
  const upload=document.querySelector('[data-blog-icon-upload]');if(!upload)return;
  const file=upload.elements.image,image=upload.querySelector('[data-blog-custom-preview]'),uploadStatus=upload.querySelector('[data-blog-upload-status]');let objectUrl;
  async function checkFile(){
    const asset=file.files[0];file.setCustomValidity('');if(objectUrl){URL.revokeObjectURL(objectUrl);objectUrl=null;}image.hidden=true;
    if(!asset)return false;
    try{
      if(asset.size>Number(upload.dataset.maxBytes))throw new Error('O ícone deve ter no máximo 100 KB.');
      const bytes=new Uint8Array(await asset.slice(0,12).arrayBuffer());
      const png=bytes.length>=8&&[137,80,78,71,13,10,26,10].every((v,i)=>bytes[i]===v);
      const webp=bytes.length>=12&&String.fromCharCode(...bytes.slice(0,4))==='RIFF'&&String.fromCharCode(...bytes.slice(8,12))==='WEBP';
      if(!png&&!webp)throw new Error('Use uma imagem PNG ou WebP válida.');
      objectUrl=URL.createObjectURL(asset);image.src=objectUrl;await image.decode().catch(()=>{throw new Error('Não foi possível ler a imagem. Escolha um PNG ou WebP válido.');});
      if(image.naturalWidth*image.naturalHeight>16000000)throw new Error('A imagem excede o limite técnico de 16 megapixels.');
      image.hidden=false;uploadStatus.textContent='Prévia pronta. Confira a imagem antes de salvar.';return true;
    }catch(error){file.setCustomValidity(error.message||'Não foi possível ler a imagem.');uploadStatus.textContent=error.message;return false;}
  }
  file.addEventListener('change',checkFile);
  upload.addEventListener('submit',async event=>{
    event.preventDefault();if(!await checkFile()){file.reportValidity();return;}
    const button=upload.querySelector('button');button.disabled=true;uploadStatus.textContent='Salvando ícone…';
    try{const response=await fetch(upload.action,{method:'POST',body:new FormData(upload),headers:{'X-Requested-With':'XMLHttpRequest'}});const result=await jsonReply(response,'Não foi possível salvar o ícone. Confira sua sessão e tente novamente.');if(!response.ok)throw new Error(result.message||'Não foi possível salvar o ícone.');const icon=result.icon;known.add(icon.identifier);let row=library.querySelector(`[data-blog-icon-row="${icon.identifier}"]`);if(!row){row=document.createElement('div');row.className='blog-icon-choice';row.dataset.blogIconRow=icon.identifier;library.appendChild(row);}row.replaceChildren();row.dataset.search=`${icon.name} ${icon.identifier}`;
      const insert=document.createElement('button');insert.type='button';insert.dataset.blogIcon=icon.identifier;const img=document.createElement('img');img.src=icon.src;img.width=img.height=28;img.alt='';img.setAttribute('aria-hidden','true');const label=document.createElement('span');label.textContent=icon.name;const token=document.createElement('small');token.textContent=`[icone:${icon.identifier}]`;label.appendChild(token);insert.append(img,label);row.appendChild(insert);
      const archive=document.createElement('button');archive.type='button';archive.className='button ghost';archive.dataset.archiveIcon=icon.archive_url;archive.textContent='Remover da biblioteca';row.appendChild(archive);uploadStatus.textContent=result.message;filter();update();
    }catch(error){uploadStatus.textContent=error.message;}
    finally{button.disabled=false;}
  });
})();
