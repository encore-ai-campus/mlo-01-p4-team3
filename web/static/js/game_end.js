/* 판 종료 화면 탭(B-10-01·B-08-01). 같은 판 화면 안에서 결과·복기·원한 단서를 바꿔 보이며 서버 명령을 보내지 않는다.
   복기·원한 단서 내용은 탭을 처음 열 때 `paldo:end-open` 이벤트로 htmx가 읽어 온다(`once`). htmx 교체 뒤에도 문서 위임으로 동작한다. */
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
    if (panel && window.htmx && panel.hasAttribute("hx-get")) window.htmx.trigger(panel, "paldo:end-open");
    if (panel && panel.getBoundingClientRect().top < 0) list.scrollIntoView({block: "start"});
  }
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
