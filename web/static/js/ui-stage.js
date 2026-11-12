/* Stage overlays retain original forms and public information. No new command transport. */
const installed=new WeakSet();
function mountStage(){
 const root=document.querySelector('#meeting');if(!root||installed.has(root))return;installed.add(root);
 const stage=root.querySelector('[data-vn-stage]');if(!stage)return;
 const actions=root.querySelector('#stage-actions');
 const dialogue=stage.querySelector('.vn-dialogue');
 const toolbar=stage.querySelector('.stage-toolbar');
 const menuToggle=root.querySelector('[data-stage-menu-toggle]');
 const rail=root.querySelector('.vn-rail');
 // 튜토리얼 판: 강조할 기록·행적표·수첩 버튼이 '게임 메뉴' 안에 숨지 않도록 도구 막대는 무대에 둔다(ui-tutorial.js).
 const keepToolbar=root.hasAttribute('data-tutorial');
 const history=document.querySelector('#update-history');
 const historyAnchor=document.createComment('update-history position');
 history?.before(historyAnchor);
 const menu=document.createElement('div');
 menu.id='stage-menu-panel';
 menu.className='stage-menu-panel';
 menu.hidden=true;
 stage.before(menu);
 menuToggle.setAttribute('aria-controls',menu.id);
 menuToggle.hidden=false;
 function closeMenu(){
  const restoreFocus=menu.contains(document.activeElement);
  menuToggle.setAttribute('aria-expanded','false');
  menu.hidden=true;
  if(restoreFocus)menuToggle.focus();
 }
 menuToggle.addEventListener('click',()=>{
  if(menuToggle.getAttribute('aria-expanded')==='true')closeMenu();
  else {menu.hidden=false;menuToggle.setAttribute('aria-expanded','true');}
 });
 function onMenuKey(event){
  if(event.key==='Escape' && !menu.hidden && !document.querySelector('dialog[open]')){
   event.preventDefault();
   closeMenu();
  }
 }
 document.addEventListener('keydown',onMenuKey);
 const toggleAnchor=document.createComment('stage-menu-toggle position');
 menuToggle.before(toggleAnchor);
 function layout(){
  if(innerWidth<900){
   if(rail.parentElement===menu){
    closeMenu();
    stage.before(toolbar);
    root.querySelector('.vn-player').before(rail);
    if(history)historyAnchor.after(history);
    toggleAnchor.after(menuToggle);
   }
   if(actions.parentElement!==stage.parentElement){
    stage.before(toolbar);
    stage.after(dialogue);
    stage.parentElement.append(actions);
   }
  } else {
   if(keepToolbar&&toolbar.parentElement!==stage)stage.querySelector('[data-vn-actor]').after(toolbar);
   if(rail.parentElement!==menu){
    if(!keepToolbar)menu.append(toolbar);
    menu.append(rail);
    if(history)menu.append(history);
    document.querySelector('.app-header .wordmark')?.after(menuToggle);
   }
   if(actions.parentElement!==stage)stage.append(dialogue,actions);
  }
 }
 layout();
 let openDialog=null,returnFocus=null;
 function fit(dialog){
  const box=(innerWidth<900?root:stage).getBoundingClientRect();
  // CSS 확대 좌표(화면 px)를 dialog가 사용하는 CSS px로 되돌린다.
  const scale=box.width/stage.offsetWidth || 1;
  if(dialog.dataset.panelId==='meeting-records' && innerWidth>=900){
   const gutter=Math.min(32,innerWidth*.022);
   dialog.style.width=`${(innerWidth-2*gutter)/scale}px`;
   dialog.style.maxHeight=`${(innerHeight-12)/scale}px`;
   dialog.style.left=`${innerWidth/(2*scale)}px`;
   dialog.style.top=`${4/scale}px`;
   return;
  }
  const top=Math.max(8,Math.min(box.top+8,innerHeight-160));
  dialog.style.width=`${Math.min(920,box.width-16,innerWidth-16)/scale}px`;
  dialog.style.maxHeight=`${Math.max(120,Math.min(box.height-16,innerHeight-top-8))/scale}px`;
  dialog.style.left=`${Math.max(8,box.left+box.width/2)/scale}px`;
  dialog.style.top=`${top/scale}px`;
 }
 function close(){if(openDialog?.open)openDialog.close();}
 const panels=new Map();
 for(const node of root.querySelectorAll('[data-stage-panel]')){
  if(node===actions)continue;
  const dialog=document.createElement('dialog');dialog.className='stage-overlay';dialog.dataset.panelId=node.id;
  const bar=document.createElement('div');bar.className='stage-overlay-heading';
  const title=document.createElement('h2');title.id=node.id+'-dialog-title';title.textContent=node.dataset.stagePanel;
  const button=document.createElement('button');button.type='button';button.textContent='닫기';if(stage.dataset.overlayCloseTip)button.title=stage.dataset.overlayCloseTip;button.addEventListener('click',()=>dialog.close());
  if(node.id==='meeting-records'){
   const backdrop=node.querySelector('[data-record-backdrop]')?.dataset.recordBackdrop;
   if(backdrop)dialog.style.setProperty('--record-backdrop',`url(${JSON.stringify(backdrop)})`);
   const brand=document.createElement('span');brand.className='record-wordmark';brand.textContent='창귀';brand.setAttribute('aria-hidden','true');
   title.classList.add('sr-only');
   bar.append(brand);
  }
  bar.append(title,button);dialog.setAttribute('aria-labelledby',title.id);dialog.append(bar,node);stage.append(dialog);panels.set(node.id,dialog);
  dialog.addEventListener('close',()=>{if(openDialog===dialog){openDialog=null;if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true});}});
 }
 function show(id,trigger){
  if(id==='stage-actions'){
   close();
   closeMenu();
   root.querySelector('[data-vn-full-close]:not([hidden])')?.click();
   const controls=actions.querySelectorAll('summary, button:not(:disabled), select:not(:disabled), textarea:not(:disabled)');
   const firstControl=Array.from(controls).find(control=>control.getClientRects().length);
   if(innerWidth<900)actions.scrollIntoView({block:'nearest'});
   (firstControl || actions).focus({preventScroll:true});
   return;
  }
  const dialog=panels.get(id);if(!dialog)return;close();returnFocus=trigger.getClientRects().length?trigger:document.activeElement;openDialog=dialog;fit(dialog);dialog.showModal();
 }
 root.addEventListener('click',event=>{
  const button=event.target.closest('[data-stage-open]');
  if(button){
   show(button.dataset.stageOpen,event.isTrusted?button:document.activeElement);
   return;
  }
  const person=event.target.closest('[data-person]');if(!person)return;
  // 무대의 인물 그림은 대사 넘김이다(ui-vn.js). 행동 메뉴는 주민 창의 인물 버튼에서만 연다.
  if(person.matches('[data-vn-actor]'))return;
  const target=root.querySelector('[data-person-actions]');target.replaceChildren();
  const heading=document.createElement('h3');
  heading.textContent=(person.textContent.trim()||person.querySelector('img')?.alt||'현재 인물')+' · 가능한 행동';target.append(heading);
  for(const form of root.querySelectorAll('#meeting-actions .command-form')){
   const kind=form.elements.kind?.value;
   if(!['suspect','defend','interrogate','vote','divine'].includes(kind))continue;
   const field=form.elements.target||form.elements.addressee;
   if(!field || (field.tagName==='SELECT'?![...field.options].some(x=>x.value===person.dataset.person):field.value!==person.dataset.person))continue;
   const choice=document.createElement('button');choice.type='button';choice.textContent=form.querySelector('button[type=submit]').textContent.trim();choice.title=form.querySelector('button[type=submit]').title;
   choice.addEventListener('click',()=>{
    field.value=person.dataset.person;
    field.dispatchEvent(new Event('change',{bubbles:true}));
    show('stage-actions',root.querySelector('[data-stage-open="stage-actions"]'));
    const details=form.closest('details');
    if(details)details.open=true;
    form.scrollIntoView({block:'nearest'});
    const control=form.querySelector('select,button[type=submit]');
    if(control?.matches('[data-divination-choice][aria-pressed]:not(:disabled)')){
     control.click();
     root.querySelector('[data-divination-submit]').focus();
    } else control?.focus({preventScroll:true});
   });target.append(choice);
  }
  if(target.children.length===1){const p=document.createElement('p');p.textContent='현재 이 인물에게 할 수 있는 행동이 없습니다.';target.append(p);}
 });
 const resize=()=>{layout();if(openDialog)fit(openDialog)};window.addEventListener('resize',resize);
 root.addEventListener('htmx:beforeCleanupElement',event=>{
  if(event.detail.elt!==root)return;
  close();
  if(history)historyAnchor.after(history);
  historyAnchor.remove();
  menuToggle.remove();
  document.removeEventListener('keydown',onMenuKey);
  window.removeEventListener('resize',resize);
 });
 root.dataset.stageReady='true';
 if(document.activeElement===document.body)root.querySelector('h1')?.focus({preventScroll:true});
}
document.addEventListener('DOMContentLoaded',mountStage);document.addEventListener('htmx:afterSwap',mountStage);
