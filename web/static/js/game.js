/* 새 행동에는 새 ID, 응답을 못 받은 동일 요청 재전송에는 같은 ID를 쓴다. */
document.addEventListener("htmx:configRequest", (event) => {
  const form = event.detail.elt.closest(".command-form");
  if (!form) return;
  const input = form.elements.command_id;
  if (!input.value) input.value = crypto.randomUUID();
  event.detail.parameters.command_id = input.value;
});
document.addEventListener("change", (event) => {
  const form = event.target.closest(".command-form");
  if (form) form.elements.command_id.value = "";
});
document.addEventListener("htmx:beforeSwap", (event) => {
  // 화면 전환으로 제거된 발언의 늦은 응답은 새 단계에 끼워 넣지 않는다.
  if (event.detail.elt?.matches("[data-job-id]") && !event.detail.elt.isConnected) {
    event.detail.shouldSwap = false;
    return;
  }
  if (event.detail.target?.id === "meeting" && [409, 422].includes(event.detail.xhr.status)) {
    event.detail.shouldSwap = true;
    event.detail.isError = false;
  }
});
function showCommandRetry(event) {
  if (!event.detail.elt.closest(".command-form")) return;
  const status = document.getElementById("command-status");
  if (status) status.textContent = "응답을 확인하지 못했습니다. 같은 행동을 다시 보내거나 화면을 새로고침해 주세요.";
}
document.addEventListener("htmx:sendError", showCommandRetry);
document.addEventListener("htmx:timeout", showCommandRetry);
