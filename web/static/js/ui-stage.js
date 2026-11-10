/* Stage overlays retain original forms and public information. No new command transport. */
const installed=new WeakSet();
function mountStage(){
 const root=document.querySelector('#meeting');if(!root||installed.has(root))return;installed.add(root);
 const stage=root.querySelector('[data-vn-stage]');if(!stage)return;
 const actions=root.querySelector('#stage-actions');
 const dialogue=stage.querySelector('.vn-dialogue');
 const toolbar=stage.querySelector('.stage-toolbar');
 function layout(){
  if(innerWidth<900){if(actions.parentElement!==stage.parentElement){stage.before(toolbar);stage.after(dialogue);dialogue.after(actions);}}
  else if(actions.parentElement!==stage){stage.prepend(toolbar);stage.append(dialogue,actions);}
 }
 layout();
 let openDialog=null,returnFocus=null;
 function fit(dialog){
  const box=(innerWidth<900?root:stage).getBoundingClientRect();
  // CSS 확대 좌표(화면 px)를 dialog가 사용하는 CSS px로 되돌린다.
  const scale=box.width/stage.offsetWidth || 1;
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
  bar.append(title,button);dialog.setAttribute('aria-labelledby',title.id);dialog.append(bar,node);stage.append(dialog);panels.set(node.id,dialog);
  dialog.addEventListener('close',()=>{if(openDialog===dialog){openDialog=null;if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true});}});
 }
 function show(id,trigger){if(id==='stage-actions'){close();root.querySelector('[data-vn-full-close]:not([hidden])')?.click();actions.querySelector('button,input,select')?.focus({preventScroll:true});if(innerWidth<900)actions.scrollIntoView({block:'nearest'});return;}const dialog=panels.get(id);if(!dialog)return;close();returnFocus=trigger;openDialog=dialog;fit(dialog);dialog.showModal();}
 root.addEventListener('click',event=>{
  const button=event.target.closest('[data-stage-open]');if(button){show(button.dataset.stageOpen,button);return;}
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
   choice.addEventListener('click',()=>{field.value=person.dataset.person;field.dispatchEvent(new Event('change',{bubbles:true}));show('stage-actions',root.querySelector('[data-stage-open="stage-actions"]'));const details=form.closest('details');if(details)details.open=true;form.scrollIntoView({block:'nearest'});form.querySelector('select,button[type=submit]')?.focus({preventScroll:true});});target.append(choice);
  }
  if(target.children.length===1){const p=document.createElement('p');p.textContent='현재 이 인물에게 할 수 있는 행동이 없습니다.';target.append(p);}
 });
 const resize=()=>{layout();if(openDialog)fit(openDialog)};window.addEventListener('resize',resize);
 root.addEventListener('htmx:beforeCleanupElement',event=>{if(event.detail.elt===root){close();window.removeEventListener('resize',resize);}});
 root.dataset.stageReady='true';
 if(document.activeElement===document.body)root.querySelector('h1')?.focus({preventScroll:true});
}
document.addEventListener('DOMContentLoaded',mountStage);document.addEventListener('htmx:afterSwap',mountStage);
