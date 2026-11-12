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
    let spot = null, bubble = null, shownTarget = null, timer = null, disposed = false, placeMode = '';
    const lockedValues = new Map();

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
      locked.clear(); lockedValues.clear();
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
        for (const select of node.tagName === 'SELECT' ? [node] : node.querySelectorAll('select')) lockedValues.set(select, select.value);
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
    // 대상을 감싼 스크롤 영역들(안쪽부터). 무대 밖 행동 패널(#stage-actions)·모달 창 등.
    function scrollers(el) {
      const out = [];
      for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
        const st = getComputedStyle(p);
        if (/(auto|scroll)/.test(st.overflowY) && p.scrollHeight > p.clientHeight + 1) out.push(p);
      }
      return out;
    }
    // 대상 가운데를 실제로 누르면 대상이 눌리는가(다른 막대·창이 덮지 않았는가). 강조 표시·말풍선은 잠시 빼고 잰다.
    function reachable(target) {
      const marks = [spot, bubble].filter(Boolean);
      for (const m of marks) m.style.visibility = 'hidden';
      const r = target.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + Math.min(r.height / 2, 12);
      const hit = x >= 0 && y >= 0 && x < innerWidth && y < innerHeight ? document.elementFromPoint(x, y) : null;
      for (const m of marks) m.style.visibility = '';
      return !!hit && (hit === target || target.contains(hit) || hit.contains(target) && hit.matches('label'));
    }
    function fullyShown(target) {
      const r = target.getBoundingClientRect(), pad = 8;
      if (r.top < pad || r.bottom > innerHeight - pad) return false;
      for (const p of scrollers(target)) {
        const a = p.getBoundingClientRect();
        if (r.top < a.top + pad || r.bottom > a.bottom - pad) return false;
      }
      return reachable(target);
    }
    // 대상을 보이는 곳으로 끌어온다(2회차 시운전: 행동 패널 아래 대상이 대사 넘김 막대 밑에 깔려 클릭이 '다음 대사'로 감).
    // 안쪽 스크롤 영역부터 맞추고, 그래도 덮여 있으면 가운데로, 그다음 위쪽으로 다시 맞춘다.
    function reveal(target, host) {
      // 목록 줄 안 대상(진술 체크박스 등)은 그 줄 전체가 보여야 무엇을 고르는지 읽힌다. 창 안에서는 가운데로 끌어온다(2회차 시운전).
      const unit = target.closest('li, tr, label') || target;
      if (fullyShown(target) && (unit === target || fullyShown(unit))) return;
      if (host !== document.body) { unit.scrollIntoView({block: 'center', inline: 'nearest'}); if (reachable(target)) return; }
      const pad = 12;
      for (const p of scrollers(target)) {
        const a = p.getBoundingClientRect(), r = unit.getBoundingClientRect();
        if (r.top < a.top + pad) p.scrollTop -= a.top + pad - r.top;
        else if (r.bottom > a.bottom - pad) p.scrollTop += Math.min(r.bottom - (a.bottom - pad), r.top - (a.top + pad));
      }
      const r = target.getBoundingClientRect();
      if (r.top < pad) window.scrollBy(0, r.top - pad);
      else if (r.bottom > innerHeight - pad) window.scrollBy(0, Math.min(r.bottom - innerHeight + pad, r.top - pad));
      if (!reachable(target)) target.scrollIntoView({block: 'center', inline: 'nearest'});
      if (!reachable(target)) target.scrollIntoView({block: 'start', inline: 'nearest'});
    }
    function place(target, host, say, mode) {
      if (!spot || spot.parentElement !== host) {
        clearMarks();
        spot = document.createElement('div'); spot.className = 'tutorial-spot'; spot.setAttribute('aria-hidden', 'true');
        bubble = document.createElement('div'); bubble.className = 'tutorial-bubble'; bubble.setAttribute('role', 'note');
        host.append(spot, bubble);
      }
      placeMode = mode || '';
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
      // 창 안 대상은 가운데로 끌어와 창 가장자리에 강조 테두리가 잘리지 않게 한다(1회차 시운전: 진술표 체크박스 잘림).
      // 이미 다 보이는 대상(예: 창 머리의 닫기 버튼)은 움직이지 않는다. 비교 결과처럼 방금 끌어온 내용이 밀려나지 않게.
      if (shownTarget !== target) { shownTarget = target; reveal(target, host); }
      position(target);
    }
    // 말풍선이 가리면 안 되는 것(1회차 시운전): 대사창·읽기 버튼, 같은 양식의 다른 칸(고른 값), 목록의 이웃 줄, 확인 창 전체.
    function avoidRects(target, host) {
      const rects = [];
      const add = el => { if (visible(el) && el !== target && !el.contains(target)) rects.push(el.getBoundingClientRect()); };
      if (placeMode === 'confirm') { rects.push(host.getBoundingClientRect()); return rects; }
      for (const el of document.querySelectorAll('.vn-dialogue, .vn-reading, .vn-options')) add(el);
      const form = target.closest('form');
      if (form) for (const el of form.querySelectorAll('label, select, .evidence-picker > summary, [data-confirm-description]')) add(el);
      const row = target.closest('li, fieldset > label');
      if (row) {
        add(row.previousElementSibling); add(row.nextElementSibling);
        // 대상이 든 줄의 나머지 내용(진술 문장 등)도 가리지 않는다.
        // 가중치 6: 이웃 줄을 조금 덮는 편이 지금 고르는 줄(누구의 어떤 진술인지)을 덮는 것보다 낫다.
        for (const part of row.querySelectorAll('p, .claim-speaker > *, span')) if (visible(part) && !part.contains(target)) { const pr = part.getBoundingClientRect(); for (let k = 0; k < 6; k++) rects.push(pr); }
      }
      const panel = target.closest('.record-panel');
      if (panel) for (const el of panel.querySelectorAll('[data-record-comparison]:not([hidden]), [data-record-selection-count]')) add(el);
      // 근처의 다른 조작·줄(2회차 시운전: 발화자 줄, 다음 버튼, 진술 문장, 수첩 줄, 이웃 투표·점치기 카드, 지켜보기·제출 버튼).
      const t = target.getBoundingClientRect(), near = 360;
      const scope = target.closest('#stage-actions, dialog, [data-stage-panel]') || host;
      for (const el of scope.querySelectorAll(`${CONTROLS}, li, tr, .claim-card, [data-vn-speaker]`)) {
        if (!visible(el) || el === target || el.contains(target) || target.contains(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.bottom < t.top - near || r.top > t.bottom + near || r.right < t.left - near || r.left > t.right + near) continue;
        rects.push(r);
      }
      return rects;
    }
    const overlap = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    function position(target = shownTarget) {
      if (!spot || !target?.isConnected) return;
      const host = spot.parentElement;
      const o = origin(host, spot);
      const r = target.getBoundingClientRect(), pad = 4;
      spot.style.left = `${(r.left - pad - o.x) / o.sx}px`; spot.style.top = `${(r.top - pad - o.y) / o.sy}px`;
      spot.style.width = `${(r.width + pad * 2) / o.sx}px`; spot.style.height = `${(r.height + pad * 2) / o.sy}px`;
      const ob = origin(host, bubble);
      const bw = bubble.offsetWidth * ob.sx, bh = bubble.offsetHeight * ob.sy;
      // 변환(transform)된 무대 창 안 말풍선은 그 창 안에 둔다(창 밖은 잘린다). 확인 창(변환 없음)은 창 밖 화면 전체를 쓴다.
      const box = host === document.body || placeMode === 'confirm' ? {left: 0, top: 0, right: innerWidth, bottom: innerHeight} : host.getBoundingClientRect();
      // 확인 창은 창 전체를 기준으로 위·아래에 둔다(창 안 요약을 가리지 않게).
      const anchor = placeMode === 'confirm' ? host.getBoundingClientRect() : r, gap = 12;
      const centerX = Math.max(box.left + 8, Math.min(box.right - bw - 8, anchor.left + anchor.width / 2 - bw / 2));
      const centerY = Math.max(box.top + 8, Math.min(box.bottom - bh - 8, anchor.top + anchor.height / 2 - bh / 2));
      const sides = {
        below: {left: centerX, top: anchor.bottom + gap}, above: {left: centerX, top: anchor.top - gap - bh},
        right: {left: anchor.right + gap, top: centerY}, left: {left: anchor.left - gap - bw, top: centerY},
        // 가운데 자리가 다른 조작을 덮을 때를 위한 모서리 자리(2회차 시운전).
        'below-start': {left: anchor.left, top: anchor.bottom + gap}, 'below-end': {left: anchor.right - bw, top: anchor.bottom + gap},
        'above-start': {left: anchor.left, top: anchor.top - gap - bh}, 'above-end': {left: anchor.right - bw, top: anchor.top - gap - bh},
        'left-top': {left: anchor.left - gap - bw, top: anchor.top}, 'left-bottom': {left: anchor.left - gap - bw, top: anchor.bottom - bh},
        'right-top': {left: anchor.right + gap, top: anchor.top}, 'right-bottom': {left: anchor.right + gap, top: anchor.bottom - bh},
      };
      const base = placeMode === 'list' ? ['right', 'left', 'below', 'above'] : ['below', 'above', 'right', 'left'];
      const order = [...base, ...Object.keys(sides).filter(k => !base.includes(k))];
      const avoid = avoidRects(target, host);
      let best = null;
      for (const side of order) {
        const {left, top} = sides[side];
        const rect = {left, top, right: left + bw, bottom: top + bh};
        const inside = left >= box.left + 4 && top >= box.top + 4 && rect.right <= box.right - 4 && rect.bottom <= box.bottom - 4;
        // 상자 밖은 쓰지 않는다. 대상과 겹치면 안 되고, 가릴 것과 겹친 넓이가 적은 쪽을 고른다(같으면 order 순서).
        const score = (inside ? 0 : 1e9) + overlap(rect, r) * 10 + avoid.reduce((sum, a) => sum + overlap(rect, a), 0);
        if (!best || score < best.score) best = {side, left, top, score};
      }
      const left = Math.max(box.left + 8, Math.min(box.right - bw - 8, best.left));
      const top = Math.max(box.top + 8, Math.min(box.bottom - bh - 8, best.top));
      bubble.style.left = `${(left - ob.x) / ob.sx}px`; bubble.style.top = `${(top - ob.y) / ob.sy}px`;
      bubble.dataset.side = best.side;
      // 어디에 둬도 다른 조작을 덮으면(상자 안 자리 중 겹침 > 0) 말풍선을 반투명하게 해 아래가 비치게 한다.
      bubble.classList.toggle('tutorial-bubble-faint', best.score > 0 && best.score < 1e9);
    }

    function apply() {
      if (disposed || !root.isConnected) return;
      unlock();
      if (!linesRead()) { clearMarks(); shownTarget = null; lockAllExcept([]); root.dataset.tutorialState = 'reading'; return; }
      const i = pointer(), sub = subs[i];
      // 지나간 순간 동작(open·close·click)은 기록해 둔다. 창을 닫은 뒤에도 '이미 열었음'으로 남아야 뒤로 돌아가지 않는다.
      for (let j = 0; j < i; j++) if (['open', 'close', 'click'].includes(subs[j].action)) flag(subs[j]);
      const {targets, host, confirm} = effective(sub);
      const live = targets.filter(Boolean);
      root.dataset.tutorialState = confirm ? 'confirm' : 'active';
      root.dataset.tutorialSub = sub.key;
      if (!confirm) { lockAllExcept(live); restrictOptions(sub, live); }
      // 여러 개를 고르는 단계는 아직 안 고른 것부터 강조한다.
      const target = (sub.action === 'check' && !confirm ? live.find(t => visible(t) && !t.checked) : null) || live.find(visible);
      // 확인 창에서는 그 단계 말풍선 대신 확인 안내를 창 밖에 둔다. 목록 고르기는 이웃 줄을 가리지 않게 옆에 둔다.
      const mode = confirm ? 'confirm' : sub.action === 'check' ? 'list' : '';
      if (target) place(target, host, confirm ? (info.confirm_say || sub.say) : sub.say, mode); else clearMarks();
    }
    // 잠긴 칸은 값을 바꾸지 못한다. 자동 입력·키보드 등으로 바뀌면 잠글 때 값으로 되돌린다(1회차 시운전: 캐묻기 질문 칸).
    function revertLocked(event) {
      // 선택 칸만 되돌린다. 체크 칸은 진술표 선택이 명령 양식 근거 칸을 함께 채우는 동기화(record_tools.js)를 살린다.
      const el = event.target;
      if (el?.tagName === 'SELECT' && lockedValues.has(el) && el.value !== lockedValues.get(el)) el.value = lockedValues.get(el);
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
    const onChange = event => { revertLocked(event); schedule(); };
    document.addEventListener('change', onChange, true);
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
        document.removeEventListener('change', onChange, true);
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
