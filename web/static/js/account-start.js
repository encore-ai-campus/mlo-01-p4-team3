/* 판 시작 전 표시만 제어한다. 폼·게임 명령·저장된 판은 변경하지 않는다. */
(() => {
  const modes = [...document.querySelectorAll('[data-start-mode]')];
  for (const mode of modes) {
    mode.addEventListener('toggle', () => {
      if (!mode.open) return;
      for (const other of modes) {
        if (other !== mode) other.open = false;
      }
    });
  }
})();
