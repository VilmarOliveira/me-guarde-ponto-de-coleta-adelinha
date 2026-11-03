(() => {
 const menu=document.querySelector('.customer-area-menu');if(!menu)return;
 const media=matchMedia('(max-width:767px)');const resize=()=>{menu.open=!media.matches;};
 media.addEventListener('change',resize);resize();
})();
