/* 광고 이미지가 연속 1초 동안 절반 이상 보일 때 웹소켓 비콘을 한 번 보낸다. */
(() => {
  "use strict";
  const records = new Map();
  const sentTokens = new Set();
  function cancel(record) {
    if (record.timer !== null) window.clearTimeout(record.timer);
    record.timer = null;
  }
  function arm(record) {
    cancel(record);
    if (document.hidden || record.ratio < 0.5 || record.sent || !record.element.isConnected
        || !record.element.complete || record.element.naturalWidth === 0) return;
    record.started = performance.now();
    record.timer = window.setTimeout(() => {
      record.timer = null;
      if (document.hidden || !record.element.isConnected || record.ratio < 0.5 || record.sent
          || sentTokens.has(record.token)) return;
      if (!record.element.complete || record.element.naturalWidth === 0) return;
      if (!window.paldoWS) { arm(record); return; }
      window.paldoWS.impression({token: record.token, visible_ratio: record.ratio,
        visible_ms: Math.max(1000, Math.floor(performance.now() - record.started)), occurred_at_ms: Date.now()});
      record.sent = true;
      sentTokens.add(record.token);
      observer.unobserve(record.element);
      records.delete(record.element);
    }, 1000);
  }
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const record = records.get(entry.target);
      if (!record) continue;
      const wasVisible = record.ratio >= 0.5;
      record.ratio = entry.isIntersecting ? entry.intersectionRatio : 0;
      if (record.ratio < 0.5) cancel(record);
      else if (!wasVisible) arm(record);
    }
  }, {threshold: [0, 0.5, 1]});
  function scan() {
    for (const [element, record] of records) {
      if (!element.isConnected) { cancel(record); observer.unobserve(element); records.delete(element); }
    }
    document.querySelectorAll("[data-ad-token]").forEach(section => {
      const element = section.querySelector("img");
      if (!element || element.dataset.adObserved || sentTokens.has(section.dataset.adToken)) return;
      element.dataset.adObserved = "true";
      const record = {element, token: section.dataset.adToken, ratio: 0, timer: null, sent: false};
      records.set(element, record);
      element.addEventListener("load", () => arm(record), {once: true});
      element.addEventListener("error", () => cancel(record));
      observer.observe(element);
    });
  }
  document.addEventListener("visibilitychange", () => records.forEach(arm));
  document.addEventListener("htmx:afterSettle", scan);
  document.addEventListener("DOMContentLoaded", scan);
  window.addEventListener("pagehide", () => records.forEach(cancel));
  scan();
})();
