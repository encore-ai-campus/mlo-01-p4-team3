"""인프라: 검사 도구의 구조화 로그(README 5절). JSON 한 줄을 표준 출력과 `out/logs/<service>.jsonl`에 쓴다.

필수 필드 `ts`(UTC ISO8601), `level`, `service`, `component`, `msg`. 실패는 `level=error`와 `error_type`.
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE = "ci"


def log(component: str, level: str, msg: str, **extra) -> dict:
    """로그 한 줄을 만들어 내보내고 그 dict를 돌려준다. 로그 폴더는 `PALDO_LOG_DIR`로 바꿀 수 있다."""
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "level": level, "service": SERVICE, "component": component, "msg": msg, **extra,
    }
    line = json.dumps(record, ensure_ascii=False)
    print(line, file=sys.stdout)
    log_dir = Path(os.environ.get("PALDO_LOG_DIR", ROOT / "out" / "logs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / f"{SERVICE}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as exc:  # 로그 파일을 못 써도 검사 결과는 바꾸지 않는다
        print(json.dumps({**record, "level": "warning", "msg": "로그 파일 쓰기 실패",
                          "error_type": type(exc).__name__}, ensure_ascii=False), file=sys.stdout)
    return record
