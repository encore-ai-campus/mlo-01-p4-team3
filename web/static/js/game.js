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

/* 심문은 공개된 진술 선택과 지난 밤만 사용한다. 최종 판정은 기존 엔진이 맡는다. */
function validateInterrogation(form) {
  if (!form?.elements.interrogation_kind) return true;
  const question = form.elements.interrogation_kind;
  const whereabouts = question.value === "whereabouts";
  form.elements.night.disabled = !whereabouts;
  form.elements.slot.disabled = !whereabouts;
  const selected = [...form.querySelectorAll('input[name="evidence"]:checked')]
    .filter(input => input.value.startsWith("claim:"));
  const missingPair = question.value === "contradiction" && selected.length < 2;
  question.setCustomValidity(missingPair ? "관련 진술 두 개를 선택해 주세요." : "");
  form.querySelector('button[type="submit"]').disabled = missingPair || (whereabouts && !form.elements.night.validity.valid);
  return !missingPair;
}
function refreshInterrogations() {
  document.querySelectorAll(".command-form").forEach(validateInterrogation);
}
/* 진술을 체크하면 행적 질문의 밤·시간 칸을 그 진술의 밤·시간으로 맞춘다. 직접 고른 값은 건드리지 않는다. */
function syncWhereabouts(input) {
  const form = input?.closest?.(".command-form");
  if (!form?.elements.interrogation_kind || input.name !== "evidence" || !input.checked || !input.dataset.slot) return;
  if (![...form.elements.night.options].some(option => option.value === input.dataset.night)) return;
  form.elements.night.value = input.dataset.night;
  form.elements.slot.value = input.dataset.slot;
}
document.addEventListener("change", event => {
  syncWhereabouts(event.target);
  validateInterrogation(event.target.closest(".command-form"));
});
document.addEventListener("submit", event => {
  const form = event.target.closest(".command-form");
  if (form?.elements.interrogation_kind) {
    validateInterrogation(form);
    if (!form.reportValidity()) { event.preventDefault(); event.stopImmediatePropagation(); }
  }
}, true);
document.addEventListener("DOMContentLoaded", refreshInterrogations);
document.addEventListener("htmx:afterSwap", refreshInterrogations);
/* 명령 막대 안에서 새로 그린 발언 카드를 보이게 한다(막대 안 스크롤로 카드 확인을 끝낸다). */
document.addEventListener("htmx:afterSwap", event => {
  const cards = document.getElementById("speech-cards");
  if (cards && event.detail?.target?.id === "speech-cards" && cards.closest(".command-bar")) {
    cards.scrollIntoView({block: "nearest"});
  }
});
