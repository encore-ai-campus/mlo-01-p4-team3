/* 진술표의 표시·선택과 기존 명령 근거 연결. 판 종료 화면에만 추가 안내를 표시한다. */
(() => {
  "use strict";
  const mounted = new WeakSet();
  const mount = () => {
    const root = document.getElementById('meeting');
    if (root && !mounted.has(root)) {
      mounted.add(root);
      mountRecords(root);
    }
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount);
  else mount();
  document.addEventListener('htmx:afterSwap', mount);

  function mountRecords(root) {
    const cards=[...root.querySelectorAll('[data-record-id]')];
    const filters=[...root.querySelectorAll('[data-record-filter]')];
    const selected=()=>cards.filter(c=>c.querySelector('[data-record-select]').checked);
    const selectionGuide=root.querySelector('[data-record-selection-guide]');
    const hiddenSelection=root.querySelector('[data-record-hidden-selection]');
    const empty=root.querySelector('[data-record-empty]');
    const reset=root.querySelector('[data-record-reset]');
    function updateFilterNotes(chosen=selected()) {
      if (empty) empty.hidden=cards.some(card=>!card.hidden);
      if (reset) reset.hidden=!filters.some(input=>input.value);
      if (hiddenSelection) {
        const hidden=chosen.filter(card=>card.hidden).length;
        hiddenSelection.hidden=!hidden;
        hiddenSelection.textContent=hidden ? `선택한 진술 ${hidden}개가 현재 목록에 보이지 않습니다.` : '';
      }
    }
    function updateSelection() {
      const chosen=selected();
      root.querySelector('[data-record-selection-count]')?.replaceChildren(document.createTextNode(`선택한 진술 ${chosen.length}개`));
      const compare=root.querySelector('[data-record-compare]'); if(compare) compare.disabled=chosen.length!==2;
      if (selectionGuide) {
        const messages=['비교할 진술 2개를 선택해 주세요.', '진술을 하나 더 선택해 주세요.', '',
                        '2개만 비교할 수 있습니다. 나머지 선택을 해제해 주세요.'];
        selectionGuide.textContent=messages[Math.min(chosen.length, 3)];
        selectionGuide.hidden=chosen.length===2;
      }
      updateFilterNotes(chosen);
      const values=new Set(chosen.map(c=>c.querySelector('[data-record-select]').value));
      for (const input of root.querySelectorAll('.command-form input[name=evidence]')) {
        const checked=values.has(input.value);
        if(input.checked!==checked) { input.checked=checked; input.dispatchEvent(new Event('change',{bubbles:true})); }
      }
      root.querySelector('[data-record-comparison]')?.setAttribute('hidden','');
    }
    function filter() {
      for(const card of cards) card.hidden=filters.some(f=>f.value && card.getAttribute(`data-record-${f.dataset.recordFilter}`)!==f.value);
      const count=root.querySelector('[data-record-count]'); if(count) count.textContent=`진술 ${cards.filter(c=>!c.hidden).length}개 · 전체 ${cards.length}개`;
      updateFilterNotes();
    }
    for(const input of root.querySelectorAll('[data-record-select]')) input.addEventListener('change',updateSelection);
    for(const input of filters) input.addEventListener('change',filter);
    reset?.addEventListener('click',()=>{
      const previous=filters.find(input=>input.value);
      for (const input of filters) input.value='';
      filter();
      previous?.focus();
    });
    root.querySelector('[data-record-clear]')?.addEventListener('click',()=>{ for(const c of cards)c.querySelector('input').checked=false; updateSelection(); });
    root.querySelector('[data-record-compare]')?.addEventListener('click',()=>{
      const chosen=selected();
      const target=root.querySelector('[data-record-comparison-lines]'); if(!target || chosen.length!==2)return;
      target.replaceChildren();
      for(const card of chosen) { const p=document.createElement('p'); p.textContent=`${card.querySelector('strong').textContent} · ${card.querySelector('.claim-meta')?.textContent.trim() || ''} — ${card.querySelector('.claim-meta + p').textContent}`; target.append(p); }
      const comparison=target.closest('[data-record-comparison]');
      comparison.hidden=false;
      // 비교 결과는 목록 아래에 생기므로 보이는 곳으로 끌어온다(11/11 튜토리얼 시운전: 눌러도 변화가 안 보임).
      comparison.scrollIntoView?.({block:comparison.closest('dialog')?'start':'nearest'});
    });
    filter();
    if (selectionGuide) updateSelection();
  }
})();
