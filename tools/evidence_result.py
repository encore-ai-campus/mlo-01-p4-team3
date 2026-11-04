"""측정 결과 JSON 쓰기 도우미(README 4절 "결과 JSON 형식").

측정 스크립트는 결과를 이 모듈의 `write_result`로만 쓴다. 파일은
`docs/evidence/perf/<ID>/<이름>.json`이고, `commit`(측정 시점 HEAD)과 `generated_at`(UTC)은 이 함수가 채운다.

    from tools.evidence_result import write_result
    write_result("B-06-02", "api", {"api_p95": {"value": 412.3, "unit": "ms"}}, "python -m tools.perf.api_latency --seed 1")

계층: `build_result`(도메인, 형식 검증과 dict 생성) / `write_result`(인프라, git HEAD 읽기와 파일 쓰기).
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tools.gitrepo import ROOT, GitRepo
from tools.tasks_md import TASK_ID_RE

METRIC_KEY_RE = re.compile(r"^[a-z0-9_]+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ResultFormatError(ValueError):
    """결과 JSON 형식(README 4절)을 어겼다."""


def result_path(task: str, name: str) -> str:
    """결과 JSON의 저장소 상대 경로."""
    return f"docs/evidence/perf/{task}/{name}.json"


def build_result(task: str, name: str, metrics: dict, source: str, commit: str, generated_at: str) -> dict:
    """형식을 검증하고 결과 JSON dict를 만든다. 어기면 ResultFormatError."""
    if not TASK_ID_RE.fullmatch(task):
        raise ResultFormatError(f"태스크 ID 형식이 아님: {task!r}")
    if not NAME_RE.fullmatch(name) or name in (".", ".."):
        raise ResultFormatError(f"이름은 영문·숫자·_.- 만: {name!r}")
    if not isinstance(source, str) or not source.strip():
        raise ResultFormatError("source(측정 명령)가 비었음")
    if not isinstance(metrics, dict) or not metrics:
        raise ResultFormatError("metrics가 비었음")
    clean: dict[str, dict] = {}
    for key, item in metrics.items():
        if not isinstance(key, str) or not METRIC_KEY_RE.fullmatch(key):
            raise ResultFormatError(f"metrics 키는 영문 소문자·숫자·_ 만: {key!r}")
        if not isinstance(item, dict) or set(item) != {"value", "unit"}:
            raise ResultFormatError(f"{key}: {{'value', 'unit'}} 두 키만 둔다")
        value, unit = item["value"], item["unit"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value \
                or value in (float("inf"), float("-inf")):
            raise ResultFormatError(f"{key}: value는 숫자(정수 또는 소수)만: {value!r}")
        if not isinstance(unit, str):
            raise ResultFormatError(f"{key}: unit은 문자열(단위 없으면 \"\")")
        clean[key] = {"value": value, "unit": unit}
    return {"task": task, "commit": commit, "generated_at": generated_at, "source": source, "metrics": clean}


def write_result(task: str, name: str, metrics: dict, source: str, root: Path | str = ROOT) -> Path:
    """결과 JSON을 `docs/evidence/perf/<task>/<name>.json`에 쓰고 경로를 돌려준다."""
    repo = GitRepo(root)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = build_result(task, name, metrics, source, repo.head(), now)
    path = Path(root) / result_path(task, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path
