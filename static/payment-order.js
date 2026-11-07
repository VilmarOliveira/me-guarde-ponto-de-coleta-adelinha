(() => {
  const body=document.querySelector('#payment-order-rows');if(!body)return;
  const status=document.querySelector('#payment-order-status');let dragged=null,touch=null,scrollFrame=null;
  const rows=()=>[...body.querySelectorAll('[data-payment-row]')];
  function renumber(row){
    rows().forEach((item,i)=>{item.querySelector('[name^=priority_]').value=String(i+1);item.querySelector('.payment-order-number').textContent=i+1;});
    if(row)status.textContent=`${row.dataset.method} na posição ${rows().indexOf(row)+1}. Alterações ainda não salvas.`;
  }
  function move(row,target){if(!target || row===target)return;const all=rows();body.insertBefore(row,all.indexOf(row)>all.indexOf(target)?target:target.nextElementSibling);renumber(row);}
  body.classList.add('order-ready');renumber();
  body.addEventListener('keydown',event=>{const handle=event.target.closest('.payment-grip');if(!handle || !['ArrowUp','ArrowDown'].includes(event.key))return;event.preventDefault();const row=handle.closest('[data-payment-row]');move(row,event.key==='ArrowUp'?row.previousElementSibling:row.nextElementSibling);handle.focus();});
  body.addEventListener('dragstart',event=>{const handle=event.target.closest('.payment-grip');if(!handle){event.preventDefault();return;}dragged=handle.closest('[data-payment-row]');dragged.classList.add('is-dragging');event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',dragged.dataset.method);});
  body.addEventListener('dragover',event=>{if(dragged && event.target.closest('[data-payment-row]')){event.preventDefault();event.dataTransfer.dropEffect='move';}});
  body.addEventListener('drop',event=>{const target=event.target.closest('[data-payment-row]');if(!dragged || !target)return;event.preventDefault();const row=dragged;move(row,target);row.classList.remove('is-dragging');dragged=null;row.querySelector('.payment-grip').focus();});
  body.addEventListener('dragend',()=>{if(dragged)dragged.classList.remove('is-dragging');dragged=null;});
  function touchTarget(){if(!touch)return;const hit=document.elementFromPoint(touch.x,touch.y)?.closest('[data-payment-row]');touch.target=hit && body.contains(hit)?hit:null;}
  function autoScroll(){if(!touch)return;if(!touch.moved){scrollFrame=requestAnimationFrame(autoScroll);return;}if(touch.y<64)window.scrollBy(0,-12);else if(touch.y>innerHeight-64)window.scrollBy(0,12);touchTarget();scrollFrame=requestAnimationFrame(autoScroll);}
  body.addEventListener('pointerdown',event=>{const handle=event.target.closest('.payment-grip');if(!handle || event.pointerType==='mouse')return;touch={id:event.pointerId,row:handle.closest('[data-payment-row]'),handle,target:null,x:event.clientX,y:event.clientY,startX:event.clientX,startY:event.clientY,moved:false};scrollFrame=requestAnimationFrame(autoScroll);handle.setPointerCapture(event.pointerId);handle.classList.add('touch-dragging');});
  body.addEventListener('pointermove',event=>{if(!touch || touch.id!==event.pointerId)return;event.preventDefault();touch.x=event.clientX;touch.y=event.clientY;touch.moved=touch.moved||Math.hypot(touch.x-touch.startX,touch.y-touch.startY)>6;if(touch.moved)touchTarget();});
  function finish(event){if(!touch || touch.id!==event.pointerId)return;if(event.type==='pointerup'){touch.x=event.clientX;touch.y=event.clientY;touch.moved=touch.moved||Math.hypot(touch.x-touch.startX,touch.y-touch.startY)>6;if(touch.moved)touchTarget();}const active=touch;touch=null;cancelAnimationFrame(scrollFrame);scrollFrame=null;if(event.type==='pointerup' && active.moved)move(active.row,active.target);active.handle.classList.remove('touch-dragging');if(active.handle.hasPointerCapture(event.pointerId))active.handle.releasePointerCapture(event.pointerId);active.handle.focus();}
  body.addEventListener('pointerup',finish);body.addEventListener('pointercancel',finish);body.addEventListener('lostpointercapture',finish);
})();
