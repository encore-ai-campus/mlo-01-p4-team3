/* B-11-01 판 종료 화면: 탭은 표시만 바꾸고 조회만 필요할 때 보낸다. */
(() => {
  "use strict";
  if (window.paldoEndTabs) return;
  window.paldoEndTabs = true;
  function load(panel) {
    const content = panel.querySelector("[data-end-content]");
    if (!window.htmx || !content || ["loading", "loaded"].includes(content.dataset.endState)) return;
    content.dataset.endState = "loading";
    panel.querySelector("[data-end-fallback]").hidden = true;
    const status = panel.querySelector("[data-end-status]");
    status.hidden = false;
    status.textContent = "불러오는 중입니다.";
    const retry = panel.querySelector("[data-end-retry]");
    retry.setAttribute("aria-disabled", "true");
    window.htmx.trigger(content, "paldo:end-open");
  }
  function select(tab, focus) {
    const list = tab.closest("[role=tablist]");
    if (!list) return;
    list.querySelectorAll("[role=tab]").forEach(other => {
      const on = other === tab;
      other.setAttribute("aria-selected", String(on));
      other.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(other.getAttribute("aria-controls"));
      panel.hidden = !on;
    });
    if (focus) tab.focus();
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    load(panel);
    if (!list.hidden && panel.getBoundingClientRect().top < 0) list.scrollIntoView({block: "start"});
  }
  function mount(root) {
    const list = root.querySelector(".end-tabs");
    if (!list?.hidden) return;
    for (const tab of list.querySelectorAll("[role=tab]")) {
      const panel = document.getElementById(tab.getAttribute("aria-controls"));
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tab.id);
      panel.tabIndex = 0;
    }
    const initial = location.hash === "#survey" ? "#end-tab-survey" : "#end-tab-result";
    select(list.querySelector(initial) || list.querySelector("#end-tab-result"), false);
    list.hidden = false;
  }
  function mountAll() {
    for (const root of document.querySelectorAll("#game-end")) mount(root);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mountAll);
  else mountAll();
  document.addEventListener("htmx:load", mountAll);

  document.addEventListener("htmx:afterRequest", event => {
    const content = event.detail?.elt;
    if (!content?.matches("[data-end-content]") || content.dataset.endState !== "loading") return;
    const panel = content.closest("[data-end-lookup]");
    const status = panel.querySelector("[data-end-status]");
    const retry = panel.querySelector("[data-end-retry]");
    retry.removeAttribute("aria-disabled");
    if (event.detail.successful) {
      content.dataset.endState = "loaded";
      status.hidden = true;
      if (document.activeElement === retry) panel.focus();
      retry.hidden = true;
    } else {
      content.dataset.endState = "failed";
      status.textContent = "내용을 불러오지 못했습니다.";
      retry.hidden = false;
    }
  });
  document.addEventListener("htmx:afterSwap", event => {
    if (event.detail?.target?.id !== "survey" && event.detail?.elt?.id !== "survey") return;
    const done = document.querySelector("#game-end [data-survey-done]");
    if (!done) return;
    document.getElementById("end-home-note")?.remove();
    document.querySelector(".end-home a")?.removeAttribute("aria-describedby");
    if (!done.closest("[hidden]")) done.querySelector("[data-end-result]").focus();
  });
  document.addEventListener("click", event => {
    const tab = event.target.closest(".end-tabs [role=tab]");
    if (tab) return select(tab, false);
    const retry = event.target.closest("[data-end-retry]");
    if (retry) return load(retry.closest("[data-end-lookup]"));
    const back = event.target.closest("#game-end [data-end-result]");
    if (back) {
      const result = document.getElementById("end-tab-result");
      if (result.closest("[hidden]")) return;
      event.preventDefault();
      select(result, true);
    }
  });
  document.addEventListener("keydown", event => {
    const tab = event.target.closest(".end-tabs [role=tab]");
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...tab.closest("[role=tablist]").querySelectorAll("[role=tab]")];
    let index = tabs.indexOf(tab);
    if (event.key === "ArrowLeft") index = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "ArrowRight") index = (index + 1) % tabs.length;
    else index = event.key === "Home" ? 0 : tabs.length - 1;
    event.preventDefault();
    select(tabs[index], true);
  });
})();
