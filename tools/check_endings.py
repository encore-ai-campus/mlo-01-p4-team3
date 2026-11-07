"""결말 문안 검사(E-07-05).

    python -m tools.check_endings            # content/endings_v1.json을 content/bundle_v1.json과 대조
    python -m tools.check_endings --result   # 결과 JSON(docs/evidence/perf/E-07-05/endings.json)

검사 항목(docs/contracts/content.md 1절 `Ending`, docs/contracts/domain.md 9절):
- 형식: 최상위 키는 content_version·endings, 결말마다 ending_id·title_ko·requires·lines_ko 네 키만.
- content_version이 묶음 파일과 같다.
- ending_id 집합이 정확히 `exorcism` + 묶음 `grudge_clues`의 가설마다 `haewon:<hypothesis_id>`(첫 버전은 h1 하나).
- requires: `exorcism`은 null, `haewon:<h>`는 `hypothesis:<h>`이고 그 가설이 묶음에 있다.
- lines_ko는 5~8줄, 빈 줄(공백만 있는 줄 포함) 없음. title_ko도 비지 않는다.

엔진은 결말 문안을 쓰지 않는다(게임 웹 결말 선택·종료 연출, 복기 해설이 읽는다). 그래서 `ContentBundle`과
따로 검사하고, 결과 JSON도 E-05-03의 `check_content --result`와 따로 쓴다.

계층: `load_json`(인프라, 파일 읽기) / `ending_problems`·`ending_metrics`(규칙, 순수 함수) /
`run`(조립) / `main`(인자 파싱·출력, 로그는 `tools.jsonlog`). 표준 라이브러리와 tools 패키지만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENDINGS = "content/endings_v1.json"
DEFAULT_BUNDLE = "content/bundle_v1.json"
TASK = "E-07-05"
RESULT_NAME = "endings"
EXORCISM = "exorcism"
MIN_LINES, MAX_LINES = 5, 8
TOP_KEYS = {"content_version", "endings"}
ENDING_KEYS = {"ending_id", "title_ko", "requires", "lines_ko"}
CLUE_PARTS = ("identity", "death", "target")


class EndingsLoadError(ValueError):
    """결말 파일이나 묶음 파일을 읽지 못했다(없음, JSON 아님)."""


def load_json(path: Path) -> object:
    """JSON 파일을 읽는다. 실패하면 EndingsLoadError."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise EndingsLoadError(f"{path}: {e}") from e


def expected_ending_ids(hypotheses: set[str]) -> set[str]:
    """domain.md 9절의 `endings_unlocked` 값 집합: 퇴마 + 가설마다 한풀이."""
    return {EXORCISM} | {f"haewon:{h}" for h in hypotheses}


def _blank(v: object) -> bool:
    return not isinstance(v, str) or not v.strip()


def ending_problems(data: object, hypotheses: set[str], content_version: str) -> list[str]:
    """결말 파일 dict의 규칙 위반을 사람이 읽는 문장 목록으로 돌려준다. 빈 목록이면 통과."""
    if not isinstance(data, dict):
        return ["최상위가 객체가 아님"]
    out: list[str] = []
    if set(data) != TOP_KEYS:
        out.append(f"최상위 키가 {sorted(TOP_KEYS)}가 아님: {sorted(data)}")
    if data.get("content_version") != content_version:
        out.append(f"content_version {data.get('content_version')!r} ≠ 묶음 {content_version!r}")
    endings = data.get("endings")
    if not isinstance(endings, list):
        return out + ["endings가 목록이 아님"]
    ids: list[str] = []
    for i, e in enumerate(endings):
        if not isinstance(e, dict):
            out.append(f"endings[{i}]: 객체가 아님")
            continue
        eid = e.get("ending_id")
        name = eid if isinstance(eid, str) else f"endings[{i}]"
        if isinstance(eid, str):
            ids.append(eid)
        else:
            out.append(f"{name}: ending_id가 문자열이 아님")
        if set(e) != ENDING_KEYS:
            out.append(f"{name}: 키가 {sorted(ENDING_KEYS)}가 아님: {sorted(e)}")
        if _blank(e.get("title_ko")):
            out.append(f"{name}: title_ko가 비었음")
        req = e.get("requires")
        if eid == EXORCISM:
            if req is not None:
                out.append(f"{name}: 퇴마는 requires가 null이어야 함: {req!r}")
        elif isinstance(eid, str) and eid.startswith("haewon:"):
            h = eid.removeprefix("haewon:")
            if req != f"hypothesis:{h}":
                out.append(f"{name}: requires가 'hypothesis:{h}'가 아님: {req!r}")
            elif h not in hypotheses:
                out.append(f"{name}: 묶음 grudge_clues에 없는 가설 {h!r}")
        lines = e.get("lines_ko")
        if not isinstance(lines, list):
            out.append(f"{name}: lines_ko가 목록이 아님")
            continue
        if not MIN_LINES <= len(lines) <= MAX_LINES:
            out.append(f"{name}: lines_ko {len(lines)}줄(기준 {MIN_LINES}~{MAX_LINES}줄)")
        for j, line in enumerate(lines):
            if _blank(line):
                out.append(f"{name}: lines_ko[{j}]가 빈 줄이거나 문자열이 아님")
    dup = sorted(i for i, n in Counter(ids).items() if n > 1)
    if dup:
        out.append(f"ending_id 중복: {dup}")
    want = expected_ending_ids(hypotheses)
    if set(ids) != want:
        out.append(f"ending_id 집합 {sorted(set(ids))} ≠ 기대 {sorted(want)}")
    return out


def ending_metrics(data: object, clue_parts: list[str], problems: list[str]) -> dict[str, dict]:
    """결과 JSON 지표(README 4절 형식: 키마다 value·unit). 결말별 줄 수 키는 ending_id의 ':'를 '_'로 바꾼다."""
    parts = Counter(clue_parts)
    m: dict[str, dict] = {f"clues_{p}": {"value": parts.get(p, 0), "unit": "개"} for p in CLUE_PARTS}
    endings = data.get("endings") if isinstance(data, dict) else None
    endings = [e for e in endings if isinstance(e, dict)] if isinstance(endings, list) else []
    m["endings"] = {"value": len(endings), "unit": "개"}
    for e in endings:
        eid, lines = e.get("ending_id"), e.get("lines_ko")
        if isinstance(eid, str) and isinstance(lines, list):
            m["lines_" + eid.replace(":", "_")] = {"value": len(lines), "unit": "줄"}
    m["violations"] = {"value": len(problems), "unit": "건"}
    return m


@dataclass
class Report:
    """검사 결과."""
    endings: str
    bundle: str
    problems: list[str]
    metrics: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """위반이 0이면 참."""
        return not self.problems


def run(endings_path: str = DEFAULT_ENDINGS, bundle_path: str = DEFAULT_BUNDLE) -> Report:
    """결말 파일과 묶음 파일을 읽어 대조한다. 묶음은 가설·단서 부분·content_version만 쓴다."""
    data = load_json(ROOT / endings_path)
    bundle = load_json(ROOT / bundle_path)
    if not isinstance(bundle, dict) or not isinstance(bundle.get("grudge_clues"), list):
        raise EndingsLoadError(f"{bundle_path}: grudge_clues 목록이 없음")
    clues = [c for c in bundle["grudge_clues"] if isinstance(c, dict)]
    hypotheses = {c["hypothesis_id"] for c in clues if isinstance(c.get("hypothesis_id"), str)}
    problems = ending_problems(data, hypotheses, bundle.get("content_version"))
    parts = [c.get("part") for c in clues if c.get("hypothesis_id") in hypotheses]
    return Report(endings_path, bundle_path, problems, ending_metrics(data, parts, problems))


def main(argv: list[str] | None = None) -> int:
    """명령줄 진입점. 통과하면 0."""
    ap = argparse.ArgumentParser(description="결말 문안 검사")
    ap.add_argument("--endings", default=DEFAULT_ENDINGS, help="저장소 기준 경로")
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE, help="저장소 기준 경로")
    ap.add_argument("--result", action="store_true",
                    help=f"결과 JSON을 docs/evidence/perf/{TASK}/{RESULT_NAME}.json에 쓴다")
    args = ap.parse_args(argv)
    try:
        r = run(args.endings, args.bundle)
    except EndingsLoadError as e:
        print(f"check_endings: {e}")
        log("check_endings", "error", "결말 파일 읽기 실패", error_type="EndingsLoadError", endings=args.endings)
        return 1
    for p in r.problems:
        print(f"- {p}")
    print("check_endings: " + ", ".join(f"{k}={v['value']}" for k, v in r.metrics.items()))
    if args.result:
        from tools.evidence_result import write_result
        src = f"python -m tools.check_endings --endings {r.endings} --bundle {r.bundle} --result"
        write_result(TASK, RESULT_NAME, r.metrics, src)
    print("check_endings: " + ("통과" if r.ok else "실패"))
    extra = {"endings": r.endings, "bundle": r.bundle, "violations": len(r.problems)}
    if r.ok:
        log("check_endings", "info", "결말 검사 통과", **extra)
    else:
        log("check_endings", "error", "결말 검사 실패", error_type="CheckFailed", **extra)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
