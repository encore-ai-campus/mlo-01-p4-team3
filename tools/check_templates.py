"""템플릿 대사 검사(E-06-01).

    python -m tools.check_templates            # content/bundle_v1.json의 templates 검사
    python -m tools.check_templates --result   # 결과 JSON(docs/evidence/perf/E-06-01/templates.json)

검사 항목(docs/contracts/content.md 1절·4절, 기획서 10장 "행동 유형 × 성향"):
- 템플릿 ID 고유, 성향 ID가 `temperaments`에 있음, 캐묻기(`interrogate`)만 `reaction`이 있음.
- 필수 조합의 누락·중복: 공용(`temperament_id: null`)은 `REQUIRED_COMMON`, 성향 4종은 `REQUIRED_TEMPERED`.
  행적 진술(`t_whereabouts`)은 ID로 따로 찾는 공용 1개(성향별 아님, E-06-01 PM 답 2).
- 자리표시자: 계약 1절 목록 밖 이름, 짝이 안 맞는 중괄호, 위치 인자·속성 접근·형식 지정 = 오탈자.
- 쓰임별 허용 자리표시자(`USAGE`, 아래 표). 엔진이 `format`하는 쓰임은 그 인자로 모든 변형이 실제 `format`된다.
- 캐묻기 반응은 모든 변형에 `{fact}`(계약 4절: 폴백도 같은 정보량). 행적 진술은 `{night}{slot}{place}` 모두.
- 엔진이 고르는 공용 해명(첫 공용 `explain`)의 `variants[0]`은 자리표시자 없는 완성 문장.
- 변형 수 `MIN_VARIANTS` 이상(README 7절 P-15, 잠정). 빈 변형 금지. 인물 이름·별칭을 넣지 않는다.

쓰임별 허용 자리표시자와 엔진 근거(engine/domain/engine.py, 기준 origin/dev fb06508):
| 쓰임 | 행동 유형 | 허용 | 엔진 호출 |
|---|---|---|---|
| 회의 발언 | suspect·defend·point_contradiction·silence | {target} | 438행 `template(cmd.kind).format(target=…)` |
| 해명 | explain(행적 진술 제외) | 없음 | 438행 회의 발언 + 479·481행 `format` 없이 캐묻기 사실 문장 |
| 캐묻기 반응 | interrogate | {fact} 필수 | 486행 `template(INTERROGATE, reaction).format(fact=…)` |
| 행적 진술 | `t_whereabouts`(ID) | {night}{slot}{place} 모두 | 520~523행 `format(night=…, slot=…, place=…)` |
| 점괘 공개 | reveal_divination | {target}{result} | 사용 경로 없음(무당 전용). 데이터 형식만 |
엔진은 (행동 유형, 반응)이 맞는 첫 공용 템플릿의 `variants[0]`만 쓴다(397~401행). 성향별 템플릿과 2번째 변형부터는
엔진이 고르는 경로가 생길 때를 위해 같은 규칙으로 미리 검사한다.

계층: `placeholder_fields`·`template_problems`(도메인 규칙, 문자열·숫자만 다룸) / `load_bundle`(인프라,
`tools.check_content` 재사용) / `run`(응용) / `main`(표현, 로그는 `tools.jsonlog`). 표준 라이브러리와 engine 패키지만 쓴다.
"""
from __future__ import annotations

import argparse
import string
import sys
from collections import Counter
from dataclasses import dataclass, field

from engine.domain.content import ContentBundle, TemplateLine
from engine.domain.model import CommandKind, ReactionIntent
from tools.check_content import DEFAULT_BUNDLE, ROOT, ContentLoadError, load_bundle
from tools.jsonlog import log

TASK = "E-06-01"
MIN_VARIANTS = 2                      # README 7절 P-15(잠정)
WHEREABOUTS_ID = "t_whereabouts"
PLACEHOLDERS = frozenset({"target", "place", "slot", "night", "addressee", "fact", "result"})   # 계약 1절

K = CommandKind
R = ReactionIntent
_MEETING = (K.SUSPECT, K.DEFEND, K.POINT_CONTRADICTION, K.EXPLAIN, K.SILENCE)
_REACTIONS = (R.EXPLAIN, R.EVADE, R.COUNTER, R.CONFESS)
REQUIRED_TEMPERED: tuple[tuple[CommandKind, ReactionIntent | None], ...] = (
    tuple((k, None) for k in _MEETING) + tuple((K.INTERROGATE, r) for r in _REACTIONS))
REQUIRED_COMMON = REQUIRED_TEMPERED + ((K.REVEAL_DIVINATION, None),)


@dataclass(frozen=True)
class Usage:
    """한 쓰임의 자리표시자 규칙. `engine_args`가 None이면 엔진이 `format`하지 않는다(형식만 검사)."""
    allowed: frozenset[str]
    required: frozenset[str] = frozenset()
    engine_args: dict | None = None


_SAMPLE = {"target": "아무개", "fact": "그날 밤엔 집에 있었소.", "night": 1, "slot": "한밤", "place": "주막"}
USAGE: dict[str, Usage] = {
    "meeting": Usage(frozenset({"target"}), engine_args={"target": _SAMPLE["target"]}),
    "explain": Usage(frozenset(), engine_args={"target": _SAMPLE["target"]}),
    "interrogate": Usage(frozenset({"fact"}), frozenset({"fact"}), {"fact": _SAMPLE["fact"]}),
    "whereabouts": Usage(frozenset({"night", "slot", "place"}), frozenset({"night", "slot", "place"}),
                         {k: _SAMPLE[k] for k in ("night", "slot", "place")}),
    "reveal": Usage(frozenset({"target", "result"})),
}


def usage_of(t: TemplateLine) -> str:
    """템플릿의 쓰임 이름(`USAGE` 키)."""
    if t.template_id == WHEREABOUTS_ID:
        return "whereabouts"
    return {K.EXPLAIN: "explain", K.INTERROGATE: "interrogate", K.REVEAL_DIVINATION: "reveal"}.get(t.kind, "meeting")


@dataclass(frozen=True)
class Problem:
    """위반 하나. `code`로 개수를 센다."""
    code: str
    message: str


def placeholder_fields(text: str) -> tuple[list[str], list[str]]:
    """문장의 자리표시자 이름 목록과 형식 오류 목록을 돌려준다. 오류: 짝이 안 맞는 중괄호, 위치 인자, 속성·색인 접근, 변환·형식 지정."""
    try:
        parts = list(string.Formatter().parse(text))
    except ValueError as e:
        return [], [f"중괄호 오류({e})"]
    names, errors = [], []
    for _, name, spec, conv in parts:
        if name is None:
            continue
        if not name.isidentifier():
            errors.append(f"잘못된 자리표시자 {{{name}}}")
            continue
        if spec or conv:
            errors.append(f"형식 지정 금지 {{{name}}}")
        names.append(name)
    return names, errors


def engine_pick(templates: list[TemplateLine], kind: CommandKind, reaction: ReactionIntent | None) -> TemplateLine | None:
    """엔진 `template()`과 같은 규칙으로 고른 공용 템플릿(맞는 첫 공용). 없으면 None."""
    return next((t for t in templates if t.kind is kind and t.reaction is reaction and t.temperament_id is None), None)


def _variant_problems(t: TemplateLine, names_ko: list[str]) -> list[Problem]:
    out: list[Problem] = []
    use = USAGE[usage_of(t)]
    if len(t.variants) < MIN_VARIANTS:
        out.append(Problem("too_few_variants", f"{t.template_id}: 변형 {len(t.variants)}개 < {MIN_VARIANTS}"))
    for i, v in enumerate(t.variants):
        where = f"{t.template_id}[{i}]"
        if not v.strip():
            out.append(Problem("empty_variant", f"{where}: 빈 변형"))
            continue
        names, errors = placeholder_fields(v)
        out += [Problem("placeholder_typo", f"{where}: {e}") for e in errors]
        unknown = sorted(set(names) - PLACEHOLDERS)
        if unknown:
            out.append(Problem("placeholder_typo", f"{where}: 계약 목록 밖 자리표시자 {unknown}"))
        bad = sorted((set(names) & PLACEHOLDERS) - use.allowed)
        if bad:
            out.append(Problem("placeholder_not_allowed", f"{where}: {usage_of(t)} 쓰임에 허용 안 됨 {bad}"))
        missing = sorted(use.required - set(names))
        if missing:
            code = "fact_missing" if "fact" in missing else "placeholder_missing"
            out.append(Problem(code, f"{where}: 필수 자리표시자 없음 {missing}"))
        if use.engine_args is not None and not errors:
            try:
                v.format(**use.engine_args)
            except (KeyError, IndexError, ValueError, AttributeError) as e:
                out.append(Problem("engine_format_error", f"{where}: 엔진 인자로 format 실패({type(e).__name__}: {e})"))
        hit = sorted(n for n in names_ko if n in v)
        if hit:
            out.append(Problem("name_in_template", f"{where}: 인물 이름·별칭 {hit}"))
    return out


def template_problems(b: ContentBundle) -> list[Problem]:
    """템플릿 규칙 위반 목록. 빈 목록이면 통과."""
    out: list[Problem] = []
    ts = b.templates
    temps = [t.temperament_id for t in b.temperaments]
    dup_ids = sorted(i for i, n in Counter(t.template_id for t in ts).items() if n > 1)
    if dup_ids:
        out.append(Problem("duplicate_id", f"템플릿 ID 중복: {dup_ids}"))
    names_ko = sorted({c.name_ko for c in b.characters} | {a for c in b.characters for a in c.aliases})
    for t in ts:
        if t.temperament_id is not None and t.temperament_id not in temps:
            out.append(Problem("unknown_temperament", f"{t.template_id}: 없는 성향 {t.temperament_id}"))
        if (t.kind is K.INTERROGATE) != (t.reaction is not None):
            out.append(Problem("reaction_mismatch", f"{t.template_id}: 캐묻기만 reaction을 둔다"))
        out += _variant_problems(t, names_ko)

    wb = [t for t in ts if t.template_id == WHEREABOUTS_ID]
    if not wb:
        out.append(Problem("missing_combo", f"{WHEREABOUTS_ID} 없음"))
    elif wb[0].kind is not K.EXPLAIN or wb[0].temperament_id is not None or wb[0].reaction is not None:
        out.append(Problem("whereabouts_fields", f"{WHEREABOUTS_ID}: kind=explain·공용·reaction 없음이어야 함"))

    keys = Counter((t.kind, t.reaction, t.temperament_id) for t in ts if t.template_id != WHEREABOUTS_ID)
    need = [(k, r, None) for k, r in REQUIRED_COMMON] + [(k, r, tp) for tp in temps for k, r in REQUIRED_TEMPERED]
    for key in need:
        k, r, tp = key
        label = f"{k.value}{'/' + r.value if r else ''} × {tp or '공용'}"
        if keys[key] == 0:
            out.append(Problem("missing_combo", f"누락: {label}"))
    for (k, r, tp), n in keys.items():
        if n > 1:
            out.append(Problem("duplicate_combo", f"중복 {n}개: {k.value}{'/' + r.value if r else ''} × {tp or '공용'}"))

    first_explain = engine_pick(ts, K.EXPLAIN, None)
    if first_explain is not None:
        if first_explain.template_id == WHEREABOUTS_ID:
            out.append(Problem("engine_order", f"엔진이 고르는 첫 공용 해명이 {WHEREABOUTS_ID}임(목록 순서)"))
        elif first_explain.variants and placeholder_fields(first_explain.variants[0])[0]:
            out.append(Problem("placeholder_not_allowed",
                               f"{first_explain.template_id}[0]: 캐묻기 사실 문장으로 쓰여 자리표시자 금지"))
    return out


@dataclass
class Report:
    """검사 결과."""
    bundle: str
    template_count: int
    min_variants: int
    problems: list[Problem]
    combo_table: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """위반이 0건이면 참."""
        return not self.problems

    def count(self, code: str) -> int:
        """`code` 위반 수."""
        return sum(1 for p in self.problems if p.code == code)


def combo_table(b: ContentBundle) -> dict[str, dict[str, int]]:
    """행(행동 유형[/반응]) × 열(공용·성향 ID) 템플릿 수 표. 행적 진술은 `whereabouts` 행."""
    cols = ["공용"] + [t.temperament_id for t in b.temperaments]
    table: dict[str, dict[str, int]] = {}
    for t in b.templates:
        row = "whereabouts" if t.template_id == WHEREABOUTS_ID else t.kind.value + (f"/{t.reaction.value}" if t.reaction else "")
        cells = table.setdefault(row, dict.fromkeys(cols, 0))
        col = t.temperament_id or "공용"
        cells[col] = cells.get(col, 0) + 1      # 없는 성향은 열을 더해 세고, 위반은 template_problems가 보고한다
    return table


def run(bundle_path: str = DEFAULT_BUNDLE) -> Report:
    """묶음을 읽어 템플릿을 검사한다."""
    b = load_bundle(ROOT / bundle_path)
    return Report(bundle_path, len(b.templates), min((len(t.variants) for t in b.templates), default=0),
                  template_problems(b), combo_table(b))


def main(argv: list[str] | None = None) -> int:
    """명령줄 진입점. 통과하면 0."""
    ap = argparse.ArgumentParser(description="템플릿 대사 검사")
    ap.add_argument("--bundle", default=DEFAULT_BUNDLE, help="저장소 기준 경로")
    ap.add_argument("--result", action="store_true", help=f"결과 JSON을 docs/evidence/perf/{TASK}/에 쓴다")
    args = ap.parse_args(argv)
    try:
        r = run(args.bundle)
    except ContentLoadError as e:
        print(f"check_templates: {e}")
        log("check_templates", "error", "콘텐츠 묶음 읽기 실패", error_type="ContentLoadError", bundle=args.bundle)
        return 1
    for p in r.problems:
        print(f"- [{p.code}] {p.message}")
    for row, cols in r.combo_table.items():
        print(f"  {row:<28} " + " ".join(f"{c}={n}" for c, n in cols.items()))
    print(f"check_templates: {r.bundle} 템플릿 {r.template_count}개, 최소 변형 {r.min_variants}개, 위반 {len(r.problems)}건")
    if args.result:
        from tools.evidence_result import write_result
        tempered = sum(n for cols in r.combo_table.values() for c, n in cols.items() if c != "공용")
        write_result(TASK, "templates", {
            "template_count": {"value": r.template_count, "unit": "개"},
            "tempered_template_count": {"value": tempered, "unit": "개"},
            "missing_combo_count": {"value": r.count("missing_combo"), "unit": "건"},
            "duplicate_combo_count": {"value": r.count("duplicate_combo") + r.count("duplicate_id"), "unit": "건"},
            "fact_missing_count": {"value": r.count("fact_missing"), "unit": "건"},
            "placeholder_typo_count": {"value": r.count("placeholder_typo"), "unit": "건"},
            "placeholder_not_allowed_count": {"value": r.count("placeholder_not_allowed"), "unit": "건"},
            "engine_format_error_count": {"value": r.count("engine_format_error"), "unit": "건"},
            "min_variants": {"value": r.min_variants, "unit": "개"},
            "problem_count": {"value": len(r.problems), "unit": "건"},
        }, f"python -m tools.check_templates --bundle {r.bundle} --result")
    print("check_templates: " + ("통과" if r.ok else "실패"))
    extra = {"bundle": r.bundle, "templates": r.template_count, "problems": len(r.problems)}
    if r.ok:
        log("check_templates", "info", "템플릿 검사 통과", **extra)
    else:
        log("check_templates", "error", "템플릿 검사 실패", error_type="CheckFailed", **extra)
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
