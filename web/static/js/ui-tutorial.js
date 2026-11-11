/* 튜토리얼 판의 강조·잠금(표현 계층). 서버가 그린 단계 정보(#tutorial-step JSON)대로 지금 할 하위 단계의 대상만 쓰게 한다.
   명령은 기존 양식으로만 보내고, 서버도 그 단계 명령만 받는다(tutorial_step 거부). 형식: tutorial_step_format.md 2·3절.
   - 그 단계 족제비 줄(lines)을 다 읽기 전에는 대사 넘김만 되고 나머지 조작은 잠근다.
   - 잠금은 '허용 대상 외 전부'다. 버튼 목록을 나열하지 않으므로 새 버튼(예: 투표 기권)이 생겨도 함께 잠긴다.
   - 잠금은 inert로 한다. disabled는 양식 값 전송을 막고 ui-vn.js 투표 결과 잠금(disabled)과 겹치므로 쓰지 않는다.
     예외: select의 허용 외 option은 disabled(펼친 목록에서 고를 수 없게).
   - 막(어둡게)과 말풍선은 열린 모달 창이 있으면 그 창 안에, 없으면 문서에 붙인다(모달은 최상위 층이라 바깥 막이 덮지 못함). */
(() => {
  "use strict";
  // 조작 요소만 잠근다. tabindex 묶음(.vn-player 등)은 대사 넘김까지 막으므로 넣지 않는다.
  const CONTROLS = 'button, input, select, textarea, summary, a[href]';
  // 늘 쓸 수 있는 것: 튜토리얼 막대(끝내기·다시), 대사 넘김, 대사 바로 표시, 무대 인물(넘김), 전신 컷 닫기, 말풍선 안 끝내기.
  const ALWAYS = '[data-tutorial-bar], [data-tutorial-bar] *, .vn-reading button, [data-vn-instant], [data-vn-actor], [data-vn-full-close], .tutorial-bubble, .tutorial-bubble *';
  const memory = new Map();
  const store = {
    get(key) { try { const v = sessionStorage.getItem(key); return v === null ? memory.get(key) : JSON.parse(v); } catch { return memory.get(key); } },
    set(key, value) { memory.set(key, value); try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* 탭 메모리로 둔다 */ } },
    drop(prefix, keep) {
      for (const key of [...memory.keys()]) if (key.startsWith(prefix) && key !== keep) memory.delete(key);
      try { for (let i = sessionStorage.length - 1; i >= 0; i--) { const key = sessionStorage.key(i); if (key?.startsWith(prefix) && key !== keep) sessionStorage.removeItem(key); } } catch { /* 없음 */ }
    },
  };
  let current = null;

  const visible = el => !!el && el.isConnected && el.getClientRects().length > 0;
  const all = sel => sel ? [...document.querySelectorAll(sel)] : [];
  const firstVisible = sel => all(sel).find(visible) || all(sel)[0] || null;
  const dialogOf = panel => panel ? document.querySelector(`dialog[data-panel-id="${CSS.escape(panel)}"]`) : null;
  const openStageDialog = root => root.querySelector('dialog.stage-overlay[open]');

  function mount() {
    current?.dispose();
    current = null;
    const root = document.querySelector('#meeting');
    const data = root?.querySelector('#tutorial-step');
    if (!root || !data || root.dataset.tutorial === undefined) return;
    let info;
    try { info = JSON.parse(data.textContent); } catch { return; }
    if (info.ended || !Array.isArray(info.substeps) || !info.substeps.length) return;
    current = controller(root, info);
  }

  function controller(root, info) {
    const gid = root.dataset.gameId;
    const prefix = `tutorial:${gid}:`;
    const key = prefix + info.step;
    store.drop(prefix, key);   // 서버 단계가 바뀌면 이전 하위 단계 기록은 버린다
    const flags = new Set(store.get(key) || []);
    const subs = info.substeps;
    const locked = new Set(), disabledOptions = new Set();
    let spot = null, bubble = null, shownTarget = null, timer = null, disposed = false;

    function linesRead() {
      const ids = (info.lines || []).map(l => l.line_id).filter(id => root.querySelector(`.vn-source[data-line-id="${CSS.escape(id)}"]`));
      if (!ids.length) return true;
      const seen = root.vnSeen;
      return !!seen && ids.every(id => seen.has(id));
    }
    function flag(sub) { if (!flags.has(sub.key)) { flags.add(sub.key); store.set(key, [...flags]); } }

    // 상태로 판정하는 하위 단계(toggle·check·select·mark·submit 다음 대상 존재)는 매번 다시 본다. 순간 동작(open·close·click)은 기록으로 본다.
    function satisfied(i) {
      const sub = subs[i];
      switch (sub.action) {
        case 'open': return flags.has(sub.key) || !!dialogOf(sub.panel)?.open;
        case 'close': return flags.has(sub.key) || !dialogOf(sub.panel)?.open;
        case 'click': return flags.has(sub.key);
        case 'toggle': return !!firstVisible(sub.target)?.open || all(sub.target).some(d => d.open);
        case 'check': {
          const want = new Set(sub.values || []);
          const boxes = all(sub.target);
          const on = boxes.filter(b => b.checked).map(b => b.value);
          return boxes.length > 0 && on.length === want.size && on.every(v => want.has(v));
        }
        case 'select': { const el = firstVisible(sub.target); return !!el && el.value === String(sub.value); }
        case 'mark': return all(sub.target).some(b => b.getAttribute('aria-pressed') === 'true');
        case 'submit': { const next = subs[i + 1]; return !!next && !!firstVisible(next.target); }
        default: return false;
      }
    }
    function pointer() { for (let i = 0; i < subs.length; i++) if (!satisfied(i)) return i; return subs.length - 1; }

    // 실제로 눌러야 할 요소: 대상이 닫힌 창·접힌 묶음 안이면 그것을 여는 요소가 먼저다.
    function effective(sub) {
      const confirm = document.querySelector('#ui-confirm[open]');
      if (confirm) return {targets: [confirm.querySelector('[data-confirm-submit]')], host: confirm, confirm: true};
      let targets = all(sub.target);
      if (sub.action === 'check') targets = targets.filter(b => (sub.values || []).includes(b.value));
      if (sub.action === 'toggle') targets = targets.map(d => d.querySelector(':scope > summary')).filter(Boolean);
      const open = openStageDialog(root);
      const within = sub.action === 'open' ? null : (sub.within || (sub.action === 'close' ? sub.panel : null));
      if (within) {
        const dialog = dialogOf(within);
        if (dialog && !dialog.open) return {targets: all(`[data-stage-open="${CSS.escape(within)}"]`), host: document.body, prerequisite: true};
      }
      // 다른 창이 열려 있고 대상이 그 밖이면 그 창부터 닫는다.
      if (open && !targets.some(t => open.contains(t))) {
        return {targets: [open.querySelector('.stage-overlay-heading button')], host: open, prerequisite: true};
      }
      // 접힌 details 안이면 가장 바깥 접힌 묶음의 summary부터 연다.
      const first = targets.find(t => !t.checked) || targets[0];
      if (first) {
        let closed = null;
        for (let node = first.parentElement; node && node !== root; node = node.parentElement) {
          if (node.tagName === 'DETAILS' && !node.open && !(sub.action === 'toggle' && node.querySelector(':scope > summary') === first)) closed = node;
        }
        if (closed) return {targets: [closed.querySelector(':scope > summary')], host: open || document.body, prerequisite: true};
      }
      return {targets: targets.filter(Boolean), host: open || document.body};
    }

    function unlock() {
      for (const el of locked) { el.inert = false; delete el.dataset.tutorialLocked; }
      locked.clear();
      for (const option of disabledOptions) { option.disabled = false; delete option.dataset.tutorialDisabled; }
      disabledOptions.clear();
    }
    function lockAllExcept(allowed) {
      const keep = el => el.matches(ALWAYS) || allowed.some(a => a === el || a.contains(el) || el.contains(a));
      const scope = [...root.querySelectorAll(CONTROLS), ...document.querySelectorAll('.app-header [data-records-open], .app-header [data-notebook-open]')];
      for (const el of scope) {
        if (el.closest('.vn-script, [hidden]') && !visible(el)) continue;
        if (keep(el) || el.inert) continue;
        // 라벨을 눌러 잠긴 칸을 바꾸지 못하게 감싼 라벨도 잠근다(허용 대상을 품지 않은 라벨만).
        const label = el.closest('label');
        const node = label && !allowed.some(a => label.contains(a)) && !label.matches(ALWAYS) ? label : el;
        if (node.inert) continue;
        node.inert = true; node.dataset.tutorialLocked = ''; locked.add(node);
      }
    }
    function restrictOptions(sub, allowed) {
      if (sub.action !== 'select') return;
      for (const select of allowed) {
        if (select.tagName !== 'SELECT') continue;
        for (const option of select.options) {
          if (option.value !== String(sub.value) && !option.disabled) { option.disabled = true; option.dataset.tutorialDisabled = ''; disabledOptions.add(option); }
        }
      }
    }

    function clearMarks() {
      spot?.remove(); bubble?.remove(); spot = bubble = null;
      for (const el of document.querySelectorAll('[data-tutorial-target]')) delete el.dataset.tutorialTarget;
    }
    // position:fixed 기준점은 변환(transform)된 모달 안에서 달라진다. 0·100px에 놓아 실제 위치와 배율을 잰다.
    function origin(host, el) {
      el.style.left = '0px'; el.style.top = '0px';
      const a = el.getBoundingClientRect();
      el.style.left = '100px'; el.style.top = '100px';
      const b = el.getBoundingClientRect();
      return {x: a.left, y: a.top, sx: (b.left - a.left) / 100 || 1, sy: (b.top - a.top) / 100 || 1};
    }
    function place(target, host, say) {
      if (!spot || spot.parentElement !== host) {
        clearMarks();
        spot = document.createElement('div'); spot.className = 'tutorial-spot'; spot.setAttribute('aria-hidden', 'true');
        bubble = document.createElement('div'); bubble.className = 'tutorial-bubble'; bubble.setAttribute('role', 'note');
        host.append(spot, bubble);
      }
      for (const el of document.querySelectorAll('[data-tutorial-target]')) if (el !== target) delete el.dataset.tutorialTarget;
      target.dataset.tutorialTarget = '';
      const text = say || '반짝이는 곳을 눌러 주세요.';
      if (bubble.dataset.text !== text || !bubble.firstChild) {
        bubble.dataset.text = text; bubble.replaceChildren();
        const p = document.createElement('p'); p.textContent = text; bubble.append(p);
        if (host !== document.body && info.exit_url) {   // 모달 안에서도 언제든 나갈 수 있게
          const exit = document.createElement('a'); exit.href = info.exit_url; exit.textContent = '튜토리얼 끝내기'; exit.className = 'tutorial-bubble-exit'; bubble.append(exit);
        }
      }
      if (shownTarget !== target) { shownTarget = target; target.scrollIntoView({block: 'nearest', inline: 'nearest'}); }
      position(target);
    }
    function position(target = shownTarget) {
      if (!spot || !target?.isConnected) return;
      const host = spot.parentElement;
      const o = origin(host, spot);
      const r = target.getBoundingClientRect(), pad = 4;
      spot.style.left = `${(r.left - pad - o.x) / o.sx}px`; spot.style.top = `${(r.top - pad - o.y) / o.sy}px`;
      spot.style.width = `${(r.width + pad * 2) / o.sx}px`; spot.style.height = `${(r.height + pad * 2) / o.sy}px`;
      const ob = origin(host, bubble);
      const bw = bubble.offsetWidth * ob.sx, bh = bubble.offsetHeight * ob.sy;
      const below = r.bottom + 12 + bh <= innerHeight - 8;
      const top = below ? r.bottom + 12 : Math.max(8, r.top - 12 - bh);
      const left = Math.max(8, Math.min(innerWidth - bw - 8, r.left + r.width / 2 - bw / 2));
      bubble.style.left = `${(left - ob.x) / ob.sx}px`; bubble.style.top = `${(top - ob.y) / ob.sy}px`;
      bubble.dataset.side = below ? 'below' : 'above';
    }

    function apply() {
      if (disposed || !root.isConnected) return;
      unlock();
      if (!linesRead()) { clearMarks(); shownTarget = null; lockAllExcept([]); root.dataset.tutorialState = 'reading'; return; }
      const i = pointer(), sub = subs[i];
      const {targets, host, confirm} = effective(sub);
      const live = targets.filter(Boolean);
      root.dataset.tutorialState = confirm ? 'confirm' : 'active';
      root.dataset.tutorialSub = sub.key;
      if (!confirm) { lockAllExcept(live); restrictOptions(sub, live); }
      const target = live.find(visible);
      if (target) place(target, host, sub.say); else clearMarks();
    }
    let queued = false;
    const schedule = () => { if (queued) return; queued = true; requestAnimationFrame(() => { queued = false; apply(); }); };

    function onClick(event) {
      if (!linesRead() || document.querySelector('#ui-confirm[open]')) return;
      const sub = subs[pointer()];
      if (sub.action === 'click' && event.target.closest?.(sub.target)) flag(sub);
      schedule();
    }
    // 창 열림·닫힘은 dialog의 open 속성 변화로 본다(close 이벤트는 거품이 없다).
    const observer = new MutationObserver(records => {
      for (const record of records) {
        const dialog = record.target;
        if (record.attributeName !== 'open' || dialog.tagName !== 'DIALOG') continue;
        if (!linesRead()) continue;
        const sub = subs[pointer()];
        if (sub.action === 'open' && dialog.dataset.panelId === sub.panel && dialog.open) flag(sub);
        if (sub.action === 'close' && dialog.dataset.panelId === sub.panel && !dialog.open) flag(sub);
      }
      schedule();
    });
    observer.observe(root, {attributes: true, attributeFilter: ['open'], subtree: true});
    const confirmDialog = document.querySelector('#ui-confirm');
    if (confirmDialog) observer.observe(confirmDialog, {attributes: true, attributeFilter: ['open']});
    const onScroll = () => position();
    root.addEventListener('vn:read', schedule);
    document.addEventListener('click', onClick, true);
    document.addEventListener('change', schedule, true);
    document.addEventListener('toggle', schedule, true);
    document.addEventListener('htmx:afterSwap', schedule);
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', onScroll, true);
    timer = setInterval(() => position(), 400);   // 무대 배율 변경·글꼴 로드 뒤 위치를 맞춘다
    apply();
    return {
      apply,
      dispose() {
        disposed = true; clearInterval(timer); observer.disconnect(); unlock(); clearMarks();
        root.removeEventListener('vn:read', schedule);
        document.removeEventListener('click', onClick, true);
        document.removeEventListener('change', schedule, true);
        document.removeEventListener('toggle', schedule, true);
        document.removeEventListener('htmx:afterSwap', schedule);
        window.removeEventListener('resize', schedule);
        window.removeEventListener('scroll', onScroll, true);
      },
    };
  }

  document.addEventListener('DOMContentLoaded', mount);
  document.addEventListener('htmx:afterSwap', event => { if (event.detail?.target?.id === 'meeting' || !current) mount(); });
  document.addEventListener('htmx:beforeSwap', event => { if (event.detail?.target?.id === 'meeting') { current?.dispose(); current = null; } });
  if (document.readyState !== 'loading') mount();
  window.paldoTutorial = {mount, apply: () => current?.apply()};
})();
