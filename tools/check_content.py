"""콘텐츠 묶음 검사(E-05-03).

    python -m tools.check_content                              # content/bundle_v1.json 구조 검사
    python -m tools.check_content --preset base6 --seeds 200   # + 엔진 배정기로 시드 200개 배정 검증
    python -m tools.check_content --preset base6 --seeds 200 --result   # 결과 JSON(docs/evidence/perf/E-05-03/)
    python -m tools.check_content --preset large8 --seeds 200 --result --task E-08-02   # 다른 태스크 증빙 폴더에

검사 항목(docs/contracts/content.md):
- 엄격 검증: `serde.from_jsonable(ContentBundle)` 통과, 다시 직렬화하면 원본과 같다.
- 참조: 성향·인물·단서·충돌 비밀 ID가 모두 있다. 비밀의 실제 장소 ≠ 거짓 장소.
- 별칭: 전체에서 고유하고, 한 인물의 별칭이 다른 인물 별칭의 일부 문자열이 아니다(의도 해석기가 부분 문자열로 찾는다).
- 충돌 제약: 양쪽에 모두 적는다. 같은 시간대·같은 실제 장소의 두 비밀은 서로 충돌로 적혀 있어야 한다.
- 배정: 엔진의 배정기(A-05-01 `engine.domain.engine.make_setup`)로 만든 배정이 `validate_assignment`를 통과한다.

계층: `load_bundle`(인프라, 파일 읽기) / `content_problems`·`count_violations`(도메인 규칙 호출) /
`run`(응용) / `main`(표현, 로그는 `tools.jsonlog`). 표준 라이브러리와 engine 패키지만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from engine.domain import serde
from engine.domain.content import ContentBundle, required_counts, validate_assignment
from engine.domain.model import PRESETS
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE = "content/bundle_v1.json"
TASK = "E-05-03"


class ContentLoadError(ValueError):
    """묶음 파일을 읽지 못했거나 엄격 검증·왕복 직렬화에 실패했다."""


def load_bundle(path: Path) -> ContentBundle:
    """묶음 JSON을 엄격 검증으로 읽는다. 다시 직렬화한 값이 원본과 다르면 실패."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        bundle = serde.from_jsonable(ContentBundle, data)
    except (OSError, ValueError) as e:
        raise ContentLoadError(f"{path}: {e}") from e
    if serde.to_jsonable(bundle) != data:
        raise ContentLoadError(f"{path}: 왕복 직렬화 결과가 원본과 다름")
    return bundle


def content_problems(b: ContentBundle) -> list[str]:
    """참조·별칭·충돌 제약 위반을 사람이 읽는 문장 목록으로 돌려준다. 빈 목록이면 통과."""
    out: list[str] = []
    temps = {t.temperament_id for t in b.temperaments}
    chars = {c.npc_id for c in b.characters}
    clues = {c.clue_id for c in b.grudge_clues}
    secrets = {s.secret_id: s for s in b.secrets}
    for kind, ids in (("인물", [c.npc_id for c in b.characters]), ("비밀", [s.secret_id for s in b.secrets]),
                      ("단서", [c.clue_id for c in b.grudge_clues])):
        dup = sorted(i for i, n in Counter(ids).items() if n > 1)
        if dup:
            out.append(f"{kind} ID 중복: {dup}")
    for c in b.characters:
        if c.temperament_id not in temps:
            out.append(f"인물 {c.npc_id}: 없는 성향 {c.temperament_id}")
        if not c.aliases:
            out.append(f"인물 {c.npc_id}: 별칭 없음")
    owner: dict[str, str] = {}
    for c in b.characters:
        for a in c.aliases:
            if a in owner and owner[a] != c.npc_id:
                out.append(f"별칭 중복: {a!r} ({owner[a]}, {c.npc_id})")
            owner.setdefault(a, c.npc_id)
    for a, who in owner.items():
        for other, who2 in owner.items():
            if who != who2 and a in other:
                out.append(f"별칭 겹침: {who}의 {a!r}가 {who2}의 {other!r} 안에 들어 있음")
    for s in b.secrets:
        if s.actual_place == s.false_place:
            out.append(f"비밀 {s.secret_id}: 실제 장소 = 거짓 장소")
        missing = sorted(set(s.eligible_npcs) - chars)
        if missing:
            out.append(f"비밀 {s.secret_id}: 없는 인물 {missing}")
        if not s.eligible_npcs:
            out.append(f"비밀 {s.secret_id}: 배정 가능한 인물 없음")
        if s.grudge_clue_id is not None and s.grudge_clue_id not in clues:
            out.append(f"비밀 {s.secret_id}: 없는 단서 {s.grudge_clue_id}")
        for o in s.conflicts_with:
            if o not in secrets:
                out.append(f"비밀 {s.secret_id}: 없는 충돌 비밀 {o}")
            elif s.secret_id not in secrets[o].conflicts_with:
                out.append(f"충돌 제약 한쪽만 적힘: {s.secret_id} → {o}")
    for s in b.secrets:
        for o in b.secrets:
            if (s.secret_id < o.secret_id and s.slot == o.slot and s.actual_place == o.actual_place
                    and o.secret_id not in s.conflicts_with):
                out.append(f"같은 시간대·실제 장소인데 충돌로 적지 않음: {s.secret_id}, {o.secret_id}")
    return out


def supported_presets(b: ContentBundle) -> list[str]:
    """인물·비밀 수가 `required_counts`를 채우는 프리셋 ID."""
    have = {"characters": len(b.characters), "secrets": len(b.secrets), "temperaments": len(b.temperaments)}
    return [pid for pid, p in PRESETS.items() if all(have[k] >= v for k, v in required_counts(p).items())]


def count_violations(b: ContentBundle, preset_id: str, seeds: range) -> Counter:
    """엔진 배정기로 시드마다 배정을 만들고 검증기 위반 코드를 센다. 배정 실패는 `assignment_error`."""
    from engine.domain.engine import AssignmentError, make_setup   # A-05-01 병합 전에는 없다
    preset = PRESETS[preset_id]
    bad: Counter = Counter()
    for seed in seeds:
        try:
            setup = make_setup(preset, b, seed, f"chk_{preset_id}_{seed:03d}")
        except AssignmentError:
            bad["assignment_error"] += 1
            continue
        for v in validate_assignment(preset, b, setup.assignment):
            bad[v.code] += 1
    return bad


@dataclass
class Report:
    """검사 결과."""
    bundle: str
    problems: list[str]
    presets: list[str]
    preset: str | None = None
    seeds: int = 0
    violations: Counter = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        """구조 문제와 배정 위반이 모두 0이면 참."""
        return not self.problems and sum(self.violations.values()) == 0


def run(bundle_path: str, preset: str | None = None, seeds: int = 0) -> Report:
    """묶음을 읽어 검사하고, 프리셋이 주어지면 배정 검증까지 한다."""
    b = load_bundle(ROOT / bundle_path)
    r = Report(bundle_path, content_problems(b), supported_presets(b))
    if preset is not None:
        if preset not in r.presets:
            r.problems.append(f"{preset}: 인물·비밀 수가 부족한 프리셋(지원: {r.presets})")
        else:
            r.preset, r.seeds = preset, seeds
            r.violations = count_violations(b, preset, range(seeds))
    return r


def main(argv: list[str] | None = None) -> int:
    """명령줄 진입점. 통과하면 0."""
    ap = argparse.ArgumentParser(description="콘텐츠 묶음 검사")
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE, help="저장소 기준 경로")
    ap.add_argument("--preset", default=None, help="배정 검증할 프리셋 ID(예: base6)")
    ap.add_argument("--seeds", type=int, default=200, help="배정 검증 시드 수(0부터)")
    ap.add_argument("--result", action="store_true", help="결과 JSON을 docs/evidence/perf/<태스크>/에 쓴다")
    ap.add_argument("--task", default=TASK, help=f"--result를 쓸 태스크 ID(기본 {TASK})")
    args = ap.parse_args(argv)
    try:
        r = run(args.bundle, args.preset, args.seeds)
    except ContentLoadError as e:
        print(f"check_content: {e}")
        log("check_content", "error", "콘텐츠 묶음 읽기 실패", error_type="ContentLoadError", bundle=args.bundle)
        return 1
    for p in r.problems:
        print(f"- {p}")
    print(f"check_content: {r.bundle} 지원 프리셋 {r.presets}")
    if r.preset:
        print(f"check_content: {r.preset} 시드 {r.seeds}개 위반 {sum(r.violations.values())}건 {dict(r.violations)}")
    if args.result and r.preset:
        from tools.evidence_result import write_result
        src = f"python -m tools.check_content --bundle {r.bundle} --preset {r.preset} --seeds {r.seeds} --result"
        if args.task != TASK:
            src += f" --task {args.task}"
        write_result(args.task, f"assign_{r.preset}", {
            "seeds": {"value": r.seeds, "unit": "개"},
            "violation_count": {"value": sum(r.violations.values()), "unit": "건"},
            "structure_problem_count": {"value": len(r.problems), "unit": "건"},
        }, src)
    print("check_content: " + ("통과" if r.ok else "실패"))
    extra = {"bundle": r.bundle, "preset": r.preset, "seeds": r.seeds,
             "problems": len(r.problems), "violations": sum(r.violations.values())}
    if r.ok:
        log("check_content", "info", "콘텐츠 검사 통과", **extra)
    else:
        log("check_content", "error", "콘텐츠 검사 실패", error_type="CheckFailed", **extra)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
