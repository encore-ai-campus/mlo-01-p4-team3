/* 판 종료 화면 탭(B-10-01·B-08-01). 같은 판 화면 안에서 결과·복기·원한 단서를 바꿔 보이며 서버 명령을 보내지 않는다.
   복기·원한 단서 내용은 탭을 열 때 `paldo:end-open` 이벤트로 htmx가 읽어 온다. 한 번 성공하면 다시 읽지 않고,
   실패하면 안내를 보이고 탭을 다시 누를 때 다시 요청한다. htmx 교체 뒤에도 문서 위임으로 동작한다. */
(() => {
  "use strict";
  if (window.paldoEndTabs) return;
  window.paldoEndTabs = true;
  function select(tab, focus) {
    const list = tab.closest("[role=tablist]");
    if (!list) return;
    list.querySelectorAll("[role=tab]").forEach(other => {
      const on = other === tab;
      other.setAttribute("aria-selected", String(on));
      other.tabIndex = on ? 0 : -1;
      const panel = document.getElementById(other.getAttribute("aria-controls"));
      if (panel) panel.hidden = !on;
    });
    if (focus) tab.focus();
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    if (panel && window.htmx && panel.hasAttribute("hx-get") && !panel.dataset.endState) {
      panel.dataset.endState = "loading";
      window.htmx.trigger(panel, "paldo:end-open");
    }
    if (panel && panel.getBoundingClientRect().top < 0) list.scrollIntoView({block: "start"});
  }
  document.addEventListener("htmx:afterRequest", event => {
    const panel = event.detail && event.detail.elt;
    if (!panel || !panel.classList || !panel.classList.contains("end-panel") || !panel.dataset.endState) return;
    if (event.detail.successful) {
      panel.dataset.endState = "loaded";
      return;
    }
    delete panel.dataset.endState;
    const note = document.createElement("p");
    note.className = "end-loading";
    note.setAttribute("role", "status");
    note.textContent = "불러오지 못했습니다. 탭을 다시 누르면 다시 시도합니다.";
    panel.replaceChildren(note);
  });
  document.addEventListener("click", event => {
    const tab = event.target.closest(".end-tabs [role=tab]");
    if (tab) select(tab, false);
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
