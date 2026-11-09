/* Reading, art and comparison only. All commands remain existing server forms. */
import { icon } from './ui-icons.js';

const scenes = new WeakMap();
const mounted = new WeakSet();
let currentScene = null;
const iconFor = {suspect:'search', defend:'shield', interrogate:'message-circle', point_contradiction:'shuffle', explain:'message-circle', silence:'eye', reveal_divination:'moon', vote:'vote', divine:'moon'};

const readingMemory = new Map();
function readStore(key) { try { return JSON.parse(sessionStorage.getItem(key) || 'null') ?? readingMemory.get(key); } catch { return readingMemory.get(key); } }
function saveStore(key, value) { readingMemory.set(key,value); try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* Keep tab memory across fragment swaps. */ } }
function updateChrome(root) {
  const night = root?.dataset.phase === 'night';
  const recordsButton=document.querySelector('[data-records-open]');
  const notebookButton=document.querySelector('[data-notebook-open]');
  if(recordsButton)recordsButton.hidden=!root?.querySelector('[data-stage-open="meeting-records"]');
  if(notebookButton)notebookButton.hidden=!root?.querySelector('#notebook');
  const header = document.querySelector('.app-header');
  const slot = document.querySelector('[data-help-mascot-slot]');
  if (night) { slot?.replaceChildren(); }
  else if (header) {
    if (slot && !slot.querySelector('img')) { const img=new Image(120,120); img.src=header.dataset.helpArt; img.alt='도움말을 안내하는 족제비'; img.className='help-mascot'; slot.replaceChildren(img); }
  }
}
function decorate(root=document) {
  for(const symbol of root.querySelectorAll('[data-action-symbol]')) if(!symbol.firstChild) symbol.innerHTML=icon(iconFor[symbol.dataset.actionSymbol] || 'message-circle');
  for (const form of root.querySelectorAll('.action-grid .command-form')) {
    const kind=form.querySelector('input[name=kind]')?.value;
    const button=form.querySelector('button[type=submit]');
    if (button && !button.querySelector('.ui-icon') && iconFor[kind]) {
      const glyph=document.createElement('span'); glyph.className='action-glyph'; glyph.innerHTML=icon(iconFor[kind]); button.prepend(glyph);
    }
  }
}
function mountRecords(root) {
  const cards=[...root.querySelectorAll('[data-record-id]')];
  const selected=()=>cards.filter(c=>c.querySelector('[data-record-select]').checked);
  function updateSelection() {
    const chosen=selected();
    root.querySelector('[data-record-selection-count]')?.replaceChildren(document.createTextNode(`선택한 진술 ${chosen.length}개`));
    const compare=root.querySelector('[data-record-compare]'); if(compare) compare.disabled=chosen.length!==2;
    const values=new Set(chosen.map(c=>c.querySelector('[data-record-select]').value));
    for (const input of root.querySelectorAll('.command-form input[name=evidence]')) {
      const checked=values.has(input.value);
      if(input.checked!==checked) { input.checked=checked; input.dispatchEvent(new Event('change',{bubbles:true})); }
    }
    root.querySelector('[data-record-comparison]')?.setAttribute('hidden','');
  }
  function filter() {
    const filters=[...root.querySelectorAll('[data-record-filter]')];
    for(const card of cards) card.hidden=filters.some(f=>f.value && card.getAttribute(`data-record-${f.dataset.recordFilter}`)!==f.value);
    const count=root.querySelector('[data-record-count]'); if(count) count.textContent=`진술 ${cards.filter(c=>!c.hidden).length}개 · 전체 ${cards.length}개`;
  }
  for(const input of root.querySelectorAll('[data-record-select]')) input.addEventListener('change',updateSelection);
  for(const input of root.querySelectorAll('[data-record-filter]')) input.addEventListener('change',filter);
  root.querySelector('[data-record-clear]')?.addEventListener('click',()=>{ for(const c of cards)c.querySelector('input').checked=false; updateSelection(); });
  root.querySelector('[data-record-compare]')?.addEventListener('click',()=>{
    const target=root.querySelector('[data-record-comparison-lines]'); if(!target)return;
    target.replaceChildren();
    for(const card of selected()) { const p=document.createElement('p'); p.textContent=`${card.dataset.recordId} · ${card.querySelector('strong').textContent} — ${card.querySelector('p').textContent}`; target.append(p); }
    root.querySelector('[data-record-comparison]').hidden=false;
  });
  filter();
}
function mountScene(root) {
  const player=root.querySelector('.vn-player'); if(!player)return;
  const key=`changgui:reading:${root.dataset.gameId}:${root.dataset.day}:${root.dataset.phase}`;
  const sources=[...root.querySelectorAll('.vn-source')].filter(n=>n.dataset.day===root.dataset.day && n.dataset.phase===root.dataset.phase);
  const stored=readStore(key);
  let index=Math.max(0,sources.findIndex(n=>n.dataset.lineId===stored?.line));
  const closed=new Set(stored?.closed || []);
  let timer=null;
  let instant=readStore('changgui:instant') !== false;
  player.querySelector('[data-vn-instant]').checked=instant;
  let lastText='';
  const text=player.querySelector('[data-vn-text]');
  function persist() { saveStore(key,{line:sources[index]?.dataset.lineId,closed:[...closed]}); }
  function render(announce=true, force=false) {
    if(!root.isConnected)return;
    const source=sources[index];
    const span=source?.querySelector('.vn-source-text');
    const copy=span?.cloneNode(true); copy?.querySelector('.line-attribution')?.remove();
    const line=copy?.textContent.trim() || (root.dataset.phase==='night'?'점칠 대상을 골라 주세요. 점괘는 나에게만 표시됩니다.':'공개된 기록을 읽고 행동을 선택하세요.');
    if(!announce && !force && line===lastText)return;
    lastText=line;
    clearTimeout(timer);
    const name=source?.dataset.speaker || '무당';
    const sprite=source?.dataset.sprite || (!source ? player.dataset.defaultSprite : '') || '';
    const canActor= root.dataset.phase!=='night' && !!sprite;
    const full=canActor && source?.dataset.full==='true' && !closed.has(source.dataset.lineId);
    const stage=player.querySelector('[data-vn-stage]'); stage.classList.toggle('show-full',full);
    const actor=player.querySelector('[data-vn-actor]'); actor.hidden=!canActor;
    if(source?.dataset.speakerId)actor.dataset.person=source.dataset.speakerId;else delete actor.dataset.person;
    let image=actor.querySelector('img');
    if(canActor) { if(!image){image=new Image();image.dataset.vnSprite='';actor.append(image);} if(image.getAttribute('src')!==sprite)image.src=sprite; image.alt=source?.dataset.actorName || name; } else actor.replaceChildren();
    player.querySelector('[data-vn-speaker]').textContent=full ? (source?.dataset.actorName || name) : name;
    player.querySelector('[data-vn-mobile-text]').textContent=line;
    player.querySelector('[data-vn-full-close]').hidden=!full;

    const attribution=player.querySelector('[data-vn-attribution]');
    attribution.hidden=!span?.querySelector('.line-attribution');
    attribution.textContent=attribution.hidden?'':'';
    const mobileAttribution=player.querySelector('[data-vn-mobile-attribution]');
    mobileAttribution.hidden=attribution.hidden;
    mobileAttribution.textContent=attribution.textContent;
    player.toggleAttribute('data-long',line.length>160);
    player.querySelector('[data-vn-position]').textContent=`${sources.length?index+1:1} / ${sources.length||1}`;
    player.querySelector('[data-vn-prev]').disabled=index===0;
    player.querySelector('[data-vn-next]').disabled=index>=sources.length-1 && !full && instant;
    player.querySelector('[data-vn-announcement]').textContent=`${name}. ${line}`;
    if(instant || full || !announce) text.textContent=line;
    else { let at=0; text.textContent=''; const type=()=>{if(!root.isConnected)return; text.textContent=line.slice(0,++at); if(at<line.length)timer=setTimeout(type,24);}; type(); }
    persist();
  }
  function next() {
    const source=sources[index];
    if(player.querySelector('[data-vn-stage]').classList.contains('show-full')) { if(source)closed.add(source.dataset.lineId); }
    else if(text.textContent!==lastText) {clearTimeout(timer);text.textContent=lastText;return;}
    else index=Math.min(sources.length-1,index+1);
    index=Math.max(0,index); render();
  }
  player.querySelector('[data-vn-next]').addEventListener('click',next);
  player.querySelector('[data-vn-prev]').addEventListener('click',()=>{index=Math.max(0,index-1);render();});
  player.querySelector('[data-vn-full-close]').addEventListener('click',next);
  function showTranscript() {
    root.querySelector('[data-stage-open="meeting-records"]')?.click();
    const transcript=root.querySelector('.speech-transcript');if(transcript)transcript.open=true;
  }
  player.querySelector('[data-vn-show-all]').addEventListener('click',showTranscript);
  player.querySelector('[data-vn-skip]').addEventListener('click',()=>{
    const source=sources[index];if(source)closed.add(source.dataset.lineId);
    clearTimeout(timer);render(false,true);text.textContent=lastText;
    showTranscript();
  });
  player.querySelector('[data-vn-instant]').addEventListener('change',e=>{instant=e.target.checked;saveStore('changgui:instant',instant);render();});
  const observer=new MutationObserver(()=>render(false));
  for(const source of sources) observer.observe(source,{childList:true,subtree:true,characterData:true});
  const controller={next,dispose(){clearTimeout(timer);observer.disconnect();}};
  scenes.set(root,controller); currentScene=controller; render();
}
function mount() {
  const root=document.querySelector('#meeting');
  updateChrome(root); decorate();
  if(root && !mounted.has(root)) { mounted.add(root); mountRecords(root); mountScene(root); }
}
document.addEventListener('DOMContentLoaded',mount);
document.addEventListener('htmx:afterSwap',mount);
document.addEventListener('htmx:beforeSwap',e=>{
  if(e.detail.target?.id==='meeting') { const old=document.querySelector('#meeting'); scenes.get(old)?.dispose(); currentScene=null; }
});
document.addEventListener('keydown',e=>{
  if(e.defaultPrevented || e.isComposing || e.repeat || ![' ','Enter'].includes(e.key) || document.querySelector('dialog[open]'))return;
  if(e.target.closest('input,textarea,select,button,a,summary,[contenteditable=true],.speech-card'))return;
  if(currentScene && e.target.closest('.vn-player')) { e.preventDefault(); currentScene.next(); }
});
document.addEventListener('click',e=>{
  if(e.target.closest('[data-help-open]')) document.querySelector('#ui-help')?.showModal();
  if(e.target.closest('[data-help-close]')) document.querySelector('#ui-help')?.close();
});
document.addEventListener('change',e=>{
  if(e.target.matches('[data-avatar-src]')) { const image=document.querySelector('[data-lobby-actor]'); if(image){image.src=e.target.dataset.avatarSrc;image.alt=e.target.value==='m'?'남성 무당':'여성 무당';} }
});


/* PM branch UI interactions. The original forms are the only command transport. */
let confirmationForm=null;
document.addEventListener('submit',event=>{
  const form=event.target;
  if(!form.matches('.command-form') || !form.isConnected || form.dataset.uiConfirmed==='true') {
    if(form.dataset?.uiConfirmed) delete form.dataset.uiConfirmed;
    return;
  }
  if(form.elements.kind?.value==='advance' || form.elements.reaction?.value==='cancel')return;
  if(!form.reportValidity())return;
  event.preventDefault();event.stopImmediatePropagation();
  confirmationForm=form;
  const dialog=document.querySelector('#ui-confirm');
  const labels=[form.querySelector('button[type=submit]')?.textContent.trim()||'선택한 행동'];
  for(const select of form.querySelectorAll('select')) if(!select.disabled&&select.value)labels.push(select.selectedOptions[0]?.textContent.trim());
  const evidence=[...form.querySelectorAll('input[name=evidence]:checked')].map(x=>x.value);
  if(evidence.length)labels.push('근거 '+evidence.join(', '));
  dialog.querySelector('[data-confirm-description]').textContent=labels.filter(Boolean).join(' · ');
  dialog.showModal();
},true);
document.addEventListener('click',event=>{
  if(event.target.closest('[data-confirm-cancel]')){document.querySelector('#ui-confirm').close();confirmationForm=null;}
  if(event.target.closest('[data-confirm-submit]')){
    const form=confirmationForm;confirmationForm=null;document.querySelector('#ui-confirm').close();
    if(form?.isConnected){form.dataset.uiConfirmed='true';form.requestSubmit();}
  }
  if(event.target.closest('[data-records-open]'))document.querySelector('[data-stage-open="meeting-records"]')?.click();
  if(event.target.closest('[data-notebook-open]'))document.querySelector('[data-stage-open="stage-notebook"]')?.click();
});
