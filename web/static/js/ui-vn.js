/* Reading, art and comparison only. All commands remain existing server forms. */
import { icon } from './ui-icons.js';

const scenes = new WeakMap();
const mounted = new WeakSet();
let currentScene = null;
const iconFor = {suspect:'search', defend:'shield', interrogate:'message-circle', point_contradiction:'shuffle', explain:'message-circle', silence:'eye', reveal_divination:'moon', vote:'vote', divine:'moon'};

const PHASE_ORDER=['morning','round1','interrogation','round2','vote','runoff','night'];
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
    for(const card of selected()) { const p=document.createElement('p'); p.textContent=`${card.querySelector('strong').textContent} · ${card.querySelector('.claim-meta')?.textContent.trim() || ''} — ${card.querySelector('.claim-meta + p').textContent}`; target.append(p); }
    root.querySelector('[data-record-comparison]').hidden=false;
  });
  filter();
}
// 안내 상태: 첫 판은 건너뛰기 표시가 없으면 켬, 다른 판은 켠 시점({day, phase, at})이 저장돼 있으면 켬. 저장이 막히면 탭 메모리.
// 첫 판도 1일째가 지난 뒤 다시 켜면(켠 시점 day > 1) 서버 안내 줄 대신 숨은 목록으로 판 시작 안내부터 만든다(pool 방식).
function guideState(root) {
  const gid=root.dataset.gameId, pool=root.querySelector('[data-guide-pool]');
  const first=!pool || pool.dataset.guideFirst!==undefined, skipped=readStore(`changgui:guide-skip:${gid}`)===true;
  const stored=pool ? readStore(`changgui:guide-on:${gid}`) : null, on=stored && !skipped ? stored : null;
  const usePool=!!on && (!first || Number(on.day)>1);
  return {first, skipped, on, usePool, active: first ? !skipped : !!on};
}
function saveGuide(root,on) {
  const gid=root.dataset.gameId;
  saveStore(`changgui:guide-skip:${gid}`,!on);
  if(root.querySelector('[data-guide-pool]')) saveStore(`changgui:guide-on:${gid}`,on ? {day:Number(root.dataset.day),phase:root.dataset.phase,at:Date.now()} : null);
  syncGuide(root);
}
// 도움말 스위치와 판 종료 말풍선을 지금 상태에 맞춘다. 도움말 창은 #meeting 밖에 있어 화면 조각이 바뀔 때마다 다시 맞춘다.
function syncGuide(root) {
  const toggle=document.querySelector('[data-guide-toggle]'); if(!root || !toggle)return;
  const {active}=guideState(root);
  toggle.checked=active;
  for(const bubble of root.querySelectorAll('[data-guide-end]')) bubble.hidden=!active;
}
function mountScene(root) {
  const player=root.querySelector('.vn-player'); if(!player)return;
  // 하루의 줄을 단계 순서대로 잇는다. 행동 뒤 단계가 바뀌어도 앞 단계에서 새로 생긴 반응 줄을 놓치지 않는다.
  const key=`changgui:reading:${root.dataset.gameId}:${root.dataset.day}`;
  const rank=phase=>PHASE_ORDER.indexOf(phase);
  const current=rank(root.dataset.phase);
  // 안내 켜기/끄기(11/10 사용자 결정). 첫 판은 서버가 안내 줄을 넣어 기본 켬이고, 끄면(건너뛰기 포함) 그 판의 안내 줄을 뺀다.
  // 첫 판이 아니면 숨은 안내 목록으로 줄을 만든다. 켠 순간 판 시작 안내부터, 이후 단계 안내는 그 단계에 처음 들어갈 때 나온다.
  const guideCfg=guideState(root);
  function guideLines() {
    if(!guideCfg.usePool) return [];
    const on=guideCfg.on, day=Number(root.dataset.day), made=[];
    for(const opt of root.querySelectorAll('[data-guide-pool] [data-guide-step]')) {
      const step=opt.dataset.guideStep, i=Number(opt.dataset.guideIndex);
      // 정렬 자리는 첫 판과 같게: 판 시작 안내는 그 단계 맨 앞, 첫 아침 안내는 대사 줄 묶음(.vn-script) 앞, 나머지는 그 묶음 끝(대화 기록 앞).
      let lineDay=on.day, phase=opt.dataset.guidePhase, slot='rest';
      // 켠 단계보다 앞 단계 안내는 버리지 않고 다음 날 그 단계에 처음 들어갈 때 나온다.
      if(step==='start') { phase=on.phase; slot='start'; }
      else {
        if(step==='morning2' || rank(phase)<rank(on.phase)) lineDay=on.day+1;
        if(step==='morning1') slot='lead';
      }
      if(lineDay!==day) continue;
      const node=document.createElement('p'); node.className='vn-source';
      Object.assign(node.dataset,{lineId:`guide-opt-${on.at}-${step}-${i}`,day:String(lineDay),phase,speaker:opt.dataset.speaker,guide:'true',slot,slotIndex:String(i)});
      if(opt.dataset.sprite) node.dataset.sprite=opt.dataset.sprite;
      node.append(opt.querySelector('.vn-source-text').cloneNode(true)); made.push(node);
    }
    return made;
  }
  function collect() {
    const order=new Map();
    // 꺼져 있거나 숨은 목록으로 만드는 중이면 서버가 넣은 첫 판 안내 줄은 뺀다.
    const nodes=[...root.querySelectorAll('.vn-source')].filter(n=>!((guideCfg.skipped || guideCfg.usePool) && n.dataset.guide==='true'));
    nodes.forEach((n,i)=>order.set(n,i));
    const script=[...root.querySelectorAll('.vn-script .vn-source')].filter(n=>order.has(n));
    const leadAt=script.length ? order.get(script[0])-0.5 : -0.5, restAt=script.length ? order.get(script.at(-1))+0.5 : nodes.length;
    for(const n of guideLines()) {
      const i=Number(n.dataset.slotIndex)/100;
      nodes.push(n); order.set(n,n.dataset.slot==='start' ? -1000+i : n.dataset.slot==='lead' ? leadAt-0.4+i : restAt+i);
    }
    // 한 박자 컷(실종 발표 뒤 피해자 전신 컷)은 그림만 보여 주는 줄이라 밤 화면(인물 금지)에서는 뺀다.
    return nodes.filter(n=>n.dataset.day===root.dataset.day && rank(n.dataset.phase)>=0 && rank(n.dataset.phase)<=current && !(root.dataset.phase==='night' && n.dataset.beat==='true'))
      .sort((a,b)=>rank(a.dataset.phase)-rank(b.dataset.phase)||order.get(a)-order.get(b));
  }
  const sources=collect();
  const stored=readStore(key);
  const seen=new Set(stored?.seen || []);
  // 새로고침·재연결은 이미 본 줄을 다시 강요하지 않고, 행동 뒤 새로 생긴 줄은 그 첫 줄에서 멈춘다.
  const unseen=sources.findIndex(n=>!seen.has(n.dataset.lineId));
  let index=unseen>=0 ? unseen : Math.max(0,sources.findIndex(n=>n.dataset.lineId===stored?.line));
  const closed=new Set(stored?.closed || []);
  let timer=null;
  let instant=readStore('changgui:instant') !== false;
  player.querySelector('[data-vn-instant]').checked=instant;
  let lastText='';
  const text=player.querySelector('[data-vn-text]');
  // 줄은 문장이 끝까지 보였을 때만 '본 줄'이 된다. 글자 출력 중에 화면을 다시 그리면 그 줄에서 다시 시작한다.
  function persist(done=false) { const id=sources[index]?.dataset.lineId; if(id&&done)seen.add(id); saveStore(key,{line:id,closed:[...closed],seen:[...seen]}); updateResultLock(); }
  // 투표 결과 발표 줄을 다 읽기 전에는 그 단계 행동(결선 투표·밤 점치기)을 잠근다. 서버 명령은 바꾸지 않는다.
  function updateResultLock() {
    const pending=sources.some(n=>n.dataset.result==='true' && !seen.has(n.dataset.lineId));
    const panel=root.querySelector('#stage-actions'); if(!panel)return;
    root.querySelector('[data-result-lock-notice]')?.toggleAttribute('hidden',!pending);
    for(const control of panel.querySelectorAll('.command-bar button, .command-bar input, .command-bar select, .command-bar textarea')) {
      if(pending && !control.disabled) { control.disabled=true; control.dataset.resultLocked=''; }
      else if(!pending && control.dataset.resultLocked!==undefined) { control.disabled=false; delete control.dataset.resultLocked; }
    }
  }
  function markAllSeen() { for(const n of sources)seen.add(n.dataset.lineId); persist(); }
  function render(announce=true, force=false) {
    if(!root.isConnected)return;
    const source=sources[index];
    const span=source?.querySelector('.vn-source-text');
    const copy=span?.cloneNode(true); copy?.querySelector('.line-attribution')?.remove();
    const body=copy?.textContent.trim() || (root.dataset.phase==='night'?'점칠 대상을 골라 주세요. 점괘는 나에게만 표시됩니다.':'공개된 기록을 읽고 행동을 선택하세요.');
    // 공개된「보인 기색」만 문장과 함께 표시한다. 폴링이 본문 span을 교체해도 이 표시는 보존된다.
    const tell=source?.querySelector('[data-vn-tell]')?.textContent.trim();
    const line=tell ? body + ' ' + tell : body;
    if(!announce && !force && line===lastText)return;
    lastText=line;
    clearTimeout(timer);
    // 해설 줄(화자 없음, data-speaker="")은 이름표를 비운다. 화자 칸이 아예 없는 줄만 무당으로 읽는다.
    const name=source ? (source.dataset.speaker ?? '무당') : '무당';
    const sprite=source?.dataset.sprite || (!source ? player.dataset.defaultSprite : '') || '';
    const guide=source?.dataset.guide==='true';
    // 마스코트 줄: 족제비 안내·단서 알림, 까치 실종 발표. 인물 자리 대신 무대 왼쪽 마스코트 자리에 세운다.
    const mascot=guide || source?.dataset.mascot==='true';
    // 밤 화면은 인물을 세우지 않는다. 예외: 그날 밤 감금 결과 전신 컷(추방 결과, spec 7절).
    const nightCut=source?.dataset.phase==='night' && source?.dataset.full==='true';
    const canActor=!mascot && (root.dataset.phase!=='night' || nightCut) && !!sprite;
    const full=canActor && source?.dataset.full==='true' && (source.dataset.beat==='true' || !closed.has(source.dataset.lineId));
    const stage=player.querySelector('[data-vn-stage]'); stage.classList.toggle('show-full',full);
    const actor=player.querySelector('[data-vn-actor]'); actor.hidden=!canActor;
    if(source?.dataset.speakerId)actor.dataset.person=source.dataset.speakerId;else delete actor.dataset.person;
    let image=actor.querySelector('img');
    if(canActor) { if(!image){image=new Image();image.dataset.vnSprite='';actor.append(image);} if(image.getAttribute('src')!==sprite)image.src=sprite; image.alt=source?.dataset.actorName || name; } else actor.replaceChildren();
    // 마스코트 줄(족제비 안내·단서 알림, 까치 실종 발표)만 무대 왼쪽 마스코트 자리에 그 줄의 그림을 세운다. 밤에는 그림 없이 글만.
    let guideArt=stage.querySelector('[data-vn-guide-mascot]');
    // 아침 줄을 밤 화면에서 늦게 읽어도 마스코트는 세우지 않는다(밤 화면 마스코트 금지).
    if(mascot && sprite && root.dataset.phase!=='night') {
      if(!guideArt){guideArt=new Image(256,256);guideArt.className='vn-mascot';guideArt.dataset.vnGuideMascot='';stage.append(guideArt);}
      if(guideArt.getAttribute('src')!==sprite)guideArt.src=sprite; guideArt.alt=name; guideArt.hidden=false;
    } else if(guideArt) guideArt.hidden=true;
    player.querySelector('[data-vn-guide-skip]')?.toggleAttribute('hidden',!guide);
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
    if(instant || full || !announce) { text.textContent=line; persist(!full); return; }
    let at=0; text.textContent=''; const type=()=>{if(!root.isConnected)return; text.textContent=line.slice(0,++at); if(at<line.length)timer=setTimeout(type,24); else persist(true);}; type();
    persist();
  }
  function next() {
    const source=sources[index];
    // 한 박자 컷은 대사로 다시 그리지 않는다(앞 줄 문장 반복 방지). 닫으면 다음 줄로, 마지막 줄이면 앞 줄로 간다.
    if(player.querySelector('[data-vn-stage]').classList.contains('show-full')) {
      if(source?.dataset.beat==='true') { persist(true); index=index<sources.length-1 ? index+1 : index-1; }
      else if(source) closed.add(source.dataset.lineId);
    }
    else if(text.textContent!==lastText) {clearTimeout(timer);text.textContent=lastText;persist(true);return;}
    else index=Math.min(sources.length-1,index+1);
    index=Math.max(0,index); render();
  }
  player.querySelector('[data-vn-next]').addEventListener('click',next);
  player.querySelector('[data-vn-prev]').addEventListener('click',()=>{index=Math.max(0,index-1);render();});
  player.querySelector('[data-vn-full-close]').addEventListener('click',next);
  // 무대(대사창 포함)를 한 번 누르면 한 줄만 넘긴다. 버튼·입력·열린 패널 안의 클릭은 제 일만 한다.
  // 인물 그림도 넘김이다(11/10 사용자 결정). 인물 행동 메뉴는 주민 창·행동하기에서 연다.
  player.querySelector('[data-vn-stage]').addEventListener('click',e=>{
    if(!e.target.closest('[data-vn-actor]') && e.target.closest('button,a,input,select,textarea,label,summary,details,dialog,form,[data-stage-panel],[data-person],.speech-card'))return;
    next();
  });
  // 안내를 다시 모은다. 켰을 때는 새 안내 첫 줄로, 껐을 때는 보던 자리(안내였으면 그 뒤 첫 줄)로 간다.
  function setGuide(on) {
    const before=sources[index];
    const after=sources.slice(index).find(n=>n.dataset.guide!=='true');
    saveGuide(root,on); Object.assign(guideCfg,guideState(root));
    sources.splice(0,sources.length,...collect());
    // 다시 켜면 읽은 안내도 처음부터 다시 보여 준다(첫 판도 판 시작 안내부터).
    if(on) for(const n of sources) if(n.dataset.guide==='true') seen.delete(n.dataset.lineId);
    const firstGuide=sources.findIndex(n=>n.dataset.guide==='true');
    if(on && firstGuide>=0) index=firstGuide;
    else { const keep=sources.find(n=>n.dataset.lineId===before?.dataset.lineId) || sources.find(n=>n.dataset.lineId===after?.dataset.lineId); index=keep ? sources.indexOf(keep) : Math.max(0,sources.length-1); }
    render();
  }
  player.querySelector('[data-vn-guide-skip]')?.addEventListener('click',()=>setGuide(false));
  function showTranscript() {
    markAllSeen();
    root.querySelector('[data-stage-open="meeting-records"]')?.click();
    const transcript=root.querySelector('.speech-transcript');if(transcript){transcript.open=true;transcript.scrollIntoView({block:'start'});}
  }
  player.querySelector('[data-vn-show-all]').addEventListener('click',showTranscript);
  player.querySelector('[data-vn-skip]').addEventListener('click',()=>{
    const source=sources[index];if(source)closed.add(source.dataset.lineId);
    clearTimeout(timer);render(false,true);text.textContent=lastText;persist(true);
    showTranscript();
  });
  player.querySelector('[data-vn-instant]').addEventListener('change',e=>{instant=e.target.checked;saveStore('changgui:instant',instant);render();});
  const observer=new MutationObserver(()=>render(false));
  for(const source of sources) observer.observe(source,{childList:true,subtree:true,characterData:true});
  const controller={next,setGuide,dispose(){clearTimeout(timer);observer.disconnect();}};
  scenes.set(root,controller); currentScene=controller; render();
}
function mount() {
  const root=document.querySelector('#meeting');
  updateChrome(root); decorate(); syncGuide(root);
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
  if(e.target.matches('[data-guide-toggle]')) { const root=document.querySelector('#meeting'); if(root) { if(currentScene?.setGuide) currentScene.setGuide(e.target.checked); else saveGuide(root,e.target.checked); } }
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

/* Button hover tips: the text comes from each button's title (UI_TIPS). Draw it in-page so it shows the same on every OS. */
let tipBox=null,tipOwner=null;
function hideTip(){ if(tipOwner?.dataset.tipTitle!==undefined){tipOwner.title=tipOwner.dataset.tipTitle;delete tipOwner.dataset.tipTitle;} tipOwner=null; tipBox?.setAttribute('hidden',''); }
function showTip(owner){
  if(tipOwner===owner)return; hideTip();
  // Modal dialogs sit in the top layer above an in-page box, so they keep the native title tooltip.
  const textValue=owner.getAttribute('title'); if(!textValue || owner.closest('dialog'))return;
  if(!tipBox){tipBox=document.createElement('div');tipBox.className='ui-tip';tipBox.setAttribute('role','tooltip');tipBox.hidden=true;document.body.append(tipBox);}
  tipOwner=owner; owner.dataset.tipTitle=textValue; owner.removeAttribute('title');
  tipBox.textContent=textValue; tipBox.hidden=false;
  const box=owner.getBoundingClientRect(), width=tipBox.offsetWidth, height=tipBox.offsetHeight;
  const top=box.top-height-8>=4 ? box.top-height-8 : box.bottom+8;
  tipBox.style.left=`${Math.max(4,Math.min(innerWidth-width-4,box.left+box.width/2-width/2))+scrollX}px`;
  tipBox.style.top=`${top+scrollY}px`;
}
const tipTarget=e=>e.target.closest?.('button[title],label[title],button[data-tip-title],label[data-tip-title]');
document.addEventListener('pointerover',e=>{const owner=tipTarget(e); if(owner)showTip(owner); else if(tipOwner && !tipOwner.contains(e.target))hideTip();});
document.addEventListener('focusin',e=>{const owner=tipTarget(e); if(owner && owner.matches(':focus-visible'))showTip(owner); else hideTip();});
document.addEventListener('focusout',hideTip);
document.addEventListener('pointerdown',hideTip);
document.addEventListener('htmx:beforeSwap',hideTip);

/* Notebook marks: one press saves through the existing notebook form (all entries, same PUT path).
   Saves run one at a time and each takes the form snapshot when it starts, so a late reply never overwrites a newer mark. */
let markQueue=Promise.resolve();
const markOf=value=>+value>=67?'100':+value<=33?'0':'50';   // template_tags.notebook_mark와 같은 구간
document.addEventListener('click',event=>{
  const button=event.target.closest('[data-mark-person]'); if(!button)return;
  const form=document.querySelector('[data-notebook-form]'); if(!form)return;
  const npc=button.dataset.markPerson, value=button.dataset.markValue;
  const input=form.querySelector(`[data-notebook-entry="${CSS.escape(npc)}"]`); if(!input)return;
  const previous=input.value; input.value=value;
  for(const other of document.querySelectorAll(`[data-mark-person="${CSS.escape(npc)}"]`)) other.setAttribute('aria-pressed',String(other.dataset.markValue===value));
  const say=text=>{for(const s of document.querySelectorAll('[data-mark-status]'))s.textContent=text;};
  const who=button.closest('.notebook-marks')?.getAttribute('aria-label')?.replace(' 수첩 표시','')||'';
  markQueue=markQueue.then(async()=>{
    if(!form.isConnected)return;
    let headers={'Content-Type':'application/x-www-form-urlencoded','HX-Request':'true'};
    try { headers={...headers,...JSON.parse(form.getAttribute('hx-headers')||'{}')}; } catch { /* token also travels in the form body */ }
    let ok=false;
    try {
      const response=await fetch(form.getAttribute('hx-put'),{method:'PUT',headers,body:new URLSearchParams(new FormData(form))});
      ok=response.ok;
    } catch { ok=false; }
    if(ok) { say(`${who} · ${button.textContent.trim()}으로 적었습니다.`); return; }
    if(input.value===value) {
      input.value=previous;
      for(const other of document.querySelectorAll(`[data-mark-person="${CSS.escape(npc)}"]`)) other.setAttribute('aria-pressed',String(other.dataset.markValue===markOf(previous)));
    }
    say('저장하지 못했습니다. 잠시 뒤 다시 눌러 주세요.');
  });
});
// Confidence saves (htmx form submit) join the same queue so mark and confidence saves never overlap.
document.addEventListener('htmx:confirm',event=>{
  const form=event.target; if(!form.matches?.('[data-notebook-form]'))return;
  event.preventDefault();
  markQueue=markQueue.then(()=>new Promise(done=>{
    if(!form.isConnected)return done();
    form.addEventListener('htmx:afterRequest',()=>done(),{once:true});
    event.detail.issueRequest();
  }));
});
