(() => {
  const body=document.querySelector('#payment-order-rows');if(!body)return;
  const status=document.querySelector('#payment-order-status');let dragged=null;
  const rows=()=>[...body.querySelectorAll('[data-payment-row]')];
  function renumber(announce){
    rows().forEach((row,index,all)=>{
      row.querySelector('[name^=priority_]').value=String(index+1);
      row.querySelector('.payment-order-number').textContent=index+1;
      row.querySelector('[data-move=up]').disabled=index===0;
      row.querySelector('[data-move=down]').disabled=index===all.length-1;
    });
    if(announce)status.textContent=`${announce.dataset.method} na posição ${rows().indexOf(announce)+1}. Alterações ainda não salvas.`;
  }
  body.classList.add('order-ready');renumber();
  body.addEventListener('click',event=>{
    const button=event.target.closest('[data-move]');if(!button)return;
    const row=button.closest('[data-payment-row]');
    if(button.dataset.move==='up' && row.previousElementSibling)body.insertBefore(row,row.previousElementSibling);
    else if(button.dataset.move==='down' && row.nextElementSibling)body.insertBefore(row.nextElementSibling,row);
    renumber(row);if(button.disabled)row.querySelector('.payment-grip').focus();
  });
  body.addEventListener('dragstart',event=>{
    const handle=event.target.closest('.payment-grip');if(!handle){event.preventDefault();return;}
    dragged=handle.closest('[data-payment-row]');dragged.classList.add('is-dragging');
    event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',dragged.dataset.method);
  });
  body.addEventListener('dragover',event=>{if(dragged && event.target.closest('[data-payment-row]')){event.preventDefault();event.dataTransfer.dropEffect='move';}});
  body.addEventListener('drop',event=>{
    const target=event.target.closest('[data-payment-row]');if(!dragged || !target)return;
    event.preventDefault();const moved=dragged;
    if(target!==moved){const current=rows();body.insertBefore(moved,current.indexOf(moved)>current.indexOf(target)?target:target.nextElementSibling);}
    moved.classList.remove('is-dragging');dragged=null;renumber(moved);moved.querySelector('.payment-grip').focus();
  });
  body.addEventListener('dragend',()=>{if(dragged)dragged.classList.remove('is-dragging');dragged=null;});
})();
