/* 광고 비콘 전용. 게임 명령·LLM 폴링은 기존 HTTP 경로를 유지한다. */
(() => {
  "use strict";
  if (window.paldoWS) return;
  const delays = [1000, 2000, 4000, 8000, 16000, 30000];
  const storageKey = "paldo.ad.dropped";
  let socket, reconnectTimer, drainTimer, attempt = 0, stopped = false, unauthorized = false;
  let queue = [];
  const pending = new Map();
  const droppedByReason = {queue_full: 0, page_exit: 0};
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey));
    for (const reason of Object.keys(droppedByReason)) {
      const count = saved?.[reason];
      if (Number.isSafeInteger(count) && count >= 0) droppedByReason[reason] = count;
    }
  } catch (_) {}
  function saveDropped() {
    try { sessionStorage.setItem(storageKey, JSON.stringify(droppedByReason)); } catch (_) {}
  }
  function trim() {
    if (queue.length > 20) {
      droppedByReason.queue_full += queue.length - 20;
      queue = queue.slice(-20);
      saveDropped();
    }
  }
  function drain() {
    if (socket.readyState !== WebSocket.OPEN || !queue.length || pending.size >= 20) return;
    const message = queue.shift();
    try { socket.send(JSON.stringify(message)); pending.set(message.msg_id, message); }
    catch (_) { queue.unshift(message); }
  }
  function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const connection = new WebSocket(protocol + "//" + location.host + "/ws/game/");
    socket = connection;
    connection.onopen = () => {
      if (stopped || socket !== connection) return;
      attempt = 0;
      const dropped = droppedByReason.queue_full + droppedByReason.page_exit;
      try {
        connection.send(JSON.stringify({type: "hello", dropped, dropped_by_reason: droppedByReason}));
      } catch (_) {
        connection.close();
        return;
      }
      droppedByReason.queue_full = 0;
      droppedByReason.page_exit = 0;
      saveDropped();
      clearInterval(drainTimer);
      drainTimer = setInterval(drain, 250);
    };
    connection.onmessage = event => {
      if (socket !== connection) return;
      let data;
      try { data = JSON.parse(event.data); } catch (_) { return; }
      if ((data.type === "ack" || data.type === "error") && pending.has(data.msg_id)) {
        pending.delete(data.msg_id);
      }
    };
    connection.onclose = event => {
      if (socket !== connection) return;
      clearInterval(drainTimer);
      if (event.code === 4401) {
        stopped = true;
        unauthorized = true;
        return;
      }
      queue = [...pending.values(), ...queue]; pending.clear(); trim();
      if (!stopped) reconnectTimer = setTimeout(connect, delays[Math.min(attempt++, delays.length - 1)]);
    };
  }
  window.paldoWS = {
    impression(data) {
      if (stopped) return;
      queue.push({type: "ad.impression", msg_id: crypto.randomUUID(), token: data.token,
        visible_ratio: data.visible_ratio, visible_ms: data.visible_ms, occurred_at_ms: data.occurred_at_ms});
      trim();
    }
  };
  window.addEventListener("pagehide", () => {
    stopped = true; clearTimeout(reconnectTimer); clearInterval(drainTimer);
    droppedByReason.page_exit += queue.length + pending.size;
    queue = [];
    pending.clear();
    saveDropped();
    if (socket) socket.close();
  });
  window.addEventListener("pageshow", event => {
    if (event.persisted && stopped && !unauthorized) { stopped = false; connect(); }
  });
  connect();
})();
