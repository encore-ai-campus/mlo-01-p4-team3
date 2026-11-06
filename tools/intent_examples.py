"""의도 예시 데이터(`content/intent/examples_v1.jsonl`) 검사 규칙과 집계(E-06-02).

계층:
- 규칙(도메인): `note_info`·`label_counts`·`contrast_counts`·`type_counts`·`slot_errors`·`lineage_errors`·
  `duplicate_texts`·`summarize`. 파일을 읽지 않고 행 목록만 받는다.
- 인프라: `load_rows`(JSONL 읽기, `contracts.datasets.IntentExample` 엄격 로드).
- 응용·표현: `main`(인자 파싱 → 읽기 → 집계 → `write_result`로 결과 JSON).

대조쌍 세는 법(11/6 PM 답): 한 `contrast_group`에 라벨이 둘 이상 들어 있으면, 그 그룹은 들어 있는
라벨마다 1쌍으로 센다. 라벨이 바뀌지 않는 묶음은 세지 않는다. 변형 행은 원본 그룹을 물려받으므로
사람 작성 행(`authored`)만 센다.

해소 유형 표기(11/6 PM 답): `notes`에 `type=<유형>; ctx=<맥락>:<자리표시자>`. 유형은 `NOTE_TYPES`.
현대어 입력 행(11/6 사용자 결정): `notes`에 `register=modern`. 하오체 행은 표시하지 않는다.
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from contracts.datasets import IntentExample, LabelSource
from engine.domain.serde import from_jsonable

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "content/intent/examples_v1.jsonl"

LABELS = ("suspect", "defend", "point_contradiction", "explain", "silence", "reveal_divination",
          "interrogate_whereabouts", "interrogate_contradiction", "interrogate_suspicion_reason", "unsupported")
FORMS = ("assertion", "question", "quote", "request")

# 라벨별 필수 슬롯 키(시드와 같은 평평한 구조. intent.md 1절의 divination(target, night, result) 표기와
# 다르게 night는 선택이다. 차이는 PM이 계약 문구로 맞춘다).
REQUIRED_SLOTS = {
    "suspect": {"target"},
    "defend": {"target"},
    "point_contradiction": {"claims"},
    "explain": set(),
    "silence": set(),
    "reveal_divination": {"target", "result"},
    "interrogate_whereabouts": {"addressee"},
    "interrogate_contradiction": {"addressee", "claims"},
    "interrogate_suspicion_reason": {"addressee"},
    "unsupported": set(),
}
SLOT_VALUES = {"result": {"clear", "murky"}, "slot": {"evening", "midnight"}}

NOTE_TYPES = ("alias", "josa", "unregistered_alias", "pronoun", "ellipsis")
# 문장에 인물이 드러나지 않아 해소 맥락이 필요한 유형
NEEDS_CTX = {"unregistered_alias", "pronoun", "ellipsis"}
CTX_KINDS = {"selected_npc", "last_addressee"}
# 인용 질문형의 수신자는 그 말을 한 사람(intent.md 2절). 문장에 없으므로 notes에 맥락을 적는다.
QUOTE_CTX = "quoted_speaker"
NOTE_KEYS = {"type", "ctx", "register"}
REGISTERS = {"modern"}   # 현대어 입력(반말·줄임 어미·흔한 외래어)
PLACEHOLDER = re.compile(r"<P[12]>")
VARIANT_ID = re.compile(r"^(?P<root>.+)_v(?P<n>[1-9]\d*)$")
MAX_VARIANTS = 3
VARIANT_SOURCES = {LabelSource.GENERATED, LabelSource.GENERATED_REVIEWED}


# ---------------------------------------------------------------- 규칙(도메인)
def note_info(notes: str) -> dict[str, str]:
    """`notes`의 `key=value; key=value`를 dict로 푼다. 형식이 아니면 빈 dict."""
    out = {}
    for part in notes.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def is_authored(row: IntentExample) -> bool:
    """사람 작성 원본 행인가."""
    return row.label_source is LabelSource.AUTHORED


def label_counts(rows: list[IntentExample]) -> dict[str, int]:
    """라벨별 사람 작성 행 수(10종 모두 키로 둔다)."""
    counts = Counter(r.label for r in rows if is_authored(r))
    return {label: counts.get(label, 0) for label in LABELS}


def contrast_counts(rows: list[IntentExample]) -> dict[str, int]:
    """라벨별 대조쌍 수: 그 라벨과 다른 라벨이 함께 든 `contrast_group` 개수(사람 작성 행만)."""
    groups = defaultdict(set)
    for r in rows:
        if is_authored(r) and r.contrast_group:
            groups[r.contrast_group].add(r.label)
    counts = Counter(label for labels in groups.values() if len(labels) > 1 for label in labels)
    return {label: counts.get(label, 0) for label in LABELS}


def type_counts(rows: list[IntentExample]) -> dict[str, int]:
    """해소 유형별 사람 작성 행 수(`notes`의 `type=`)."""
    counts = Counter(note_info(r.notes).get("type") for r in rows if is_authored(r))
    return {kind: counts.get(kind, 0) for kind in NOTE_TYPES}


def modern_counts(rows: list[IntentExample]) -> dict[str, int]:
    """라벨별 현대어 입력(`register=modern`) 사람 작성 행 수."""
    counts = Counter(r.label for r in rows if is_authored(r) and note_info(r.notes).get("register") == "modern")
    return {label: counts.get(label, 0) for label in LABELS}


def slot_errors(rows: list[IntentExample]) -> list[str]:
    """라벨·형태·필수 슬롯·슬롯 값·해소 표기 위반을 `example_id: 사유`로 돌려준다."""
    errors = []
    for r in rows:
        if r.label not in LABELS:
            errors.append(f"{r.example_id}: unknown label {r.label}")
            continue
        if r.form not in FORMS:
            errors.append(f"{r.example_id}: unknown form {r.form}")
        missing = REQUIRED_SLOTS[r.label] - set(r.slots)
        if missing:
            errors.append(f"{r.example_id}: missing slots {sorted(missing)}")
        for key, allowed in SLOT_VALUES.items():
            if key in r.slots and r.slots[key] not in allowed:
                errors.append(f"{r.example_id}: bad {key} {r.slots[key]}")
        for key in ("target", "addressee", "mentioned"):
            if key in r.slots and not PLACEHOLDER.fullmatch(str(r.slots[key])):
                errors.append(f"{r.example_id}: {key} must be a placeholder")
        info = note_info(r.notes)
        if r.notes and (not info or set(info) - NOTE_KEYS):
            errors.append(f"{r.example_id}: notes must be key=value with keys {sorted(NOTE_KEYS)}")
        ctx_kind, _, ctx_who = info.get("ctx", "").partition(":")
        if "ctx" in info and (ctx_kind not in CTX_KINDS | {QUOTE_CTX} or not PLACEHOLDER.fullmatch(ctx_who)):
            errors.append(f"{r.example_id}: bad ctx {info['ctx']}")
        if r.form == "quote" and (ctx_kind != QUOTE_CTX or ctx_who != r.slots.get("addressee")):
            errors.append(f"{r.example_id}: quote needs ctx=quoted_speaker:<addressee>")
        if "register" in info and info["register"] not in REGISTERS:
            errors.append(f"{r.example_id}: unknown register {info['register']}")
        kind = info.get("type")
        if kind is None:
            continue
        if kind not in NOTE_TYPES:
            errors.append(f"{r.example_id}: unknown type {kind}")
        elif kind in NEEDS_CTX:
            ctx = info.get("ctx", "")
            ctx_kind, _, who = ctx.partition(":")
            if ctx_kind not in CTX_KINDS or not PLACEHOLDER.fullmatch(who):
                errors.append(f"{r.example_id}: {kind} needs ctx=selected_npc|last_addressee:<P1>")
        elif not PLACEHOLDER.search(r.text):
            errors.append(f"{r.example_id}: {kind} row must contain a placeholder")
    return errors


def lineage_errors(rows: list[IntentExample], max_variants: int = MAX_VARIANTS) -> list[str]:
    """계보 규칙 위반: 원본은 자기 ID가 계보, 변형은 `<원본>_vN`이고 원본의 라벨·형태·슬롯·대조그룹을 물려받는다."""
    by_id = {r.example_id: r for r in rows}
    errors = []
    if len(by_id) != len(rows):
        errors.append("duplicate example_id")
    variants = Counter()
    for r in rows:
        root_id = r.split_key.lineage_root
        if is_authored(r):
            if root_id != r.example_id:
                errors.append(f"{r.example_id}: authored lineage_root must be itself")
            continue
        if r.label_source not in VARIANT_SOURCES:
            errors.append(f"{r.example_id}: unexpected label_source {r.label_source.value}")
            continue
        match = VARIANT_ID.match(r.example_id)
        root = by_id.get(root_id)
        if root is None or not is_authored(root):
            errors.append(f"{r.example_id}: lineage_root {root_id} is not an authored row")
            continue
        if not match or match["root"] != root_id:
            errors.append(f"{r.example_id}: variant id must be <root>_vN")
        variants[root_id] += 1
        for field in ("label", "form", "slots", "contrast_group"):
            if getattr(r, field) != getattr(root, field):
                errors.append(f"{r.example_id}: {field} differs from root")
        if r.text == root.text:
            errors.append(f"{r.example_id}: same text as root")
    errors += [f"{root}: {n} variants > {max_variants}" for root, n in variants.items() if n > max_variants]
    return errors


def normalize_text(text: str) -> str:
    """중복 판정용: NFC, 공백·문장부호 제거(자리표시자 글자는 남는다)."""
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if not (ch.isspace() or unicodedata.category(ch).startswith("P")))


def duplicate_texts(rows: list[IntentExample]) -> list[list[str]]:
    """정규화 문장이 같은 행 ID 묶음(2개 이상인 것만)."""
    groups = defaultdict(list)
    for r in rows:
        groups[normalize_text(r.text)].append(r.example_id)
    return [ids for ids in groups.values() if len(ids) > 1]


def summarize(rows: list[IntentExample]) -> dict:
    """결과 JSON 지표(숫자만): 라벨별 작성 수·대조쌍 수, 유형별 수, 변형 수, 위반 수."""
    sources = Counter(r.label_source.value for r in rows)
    metrics = {
        "rows": len(rows),
        "authored": sources.get(LabelSource.AUTHORED.value, 0),
        "generated": sources.get(LabelSource.GENERATED.value, 0),
        "generated_reviewed": sources.get(LabelSource.GENERATED_REVIEWED.value, 0),
        "authored_by_label": label_counts(rows),
        "contrast_pairs_by_label": contrast_counts(rows),
        "resolution_types": type_counts(rows),
        "modern_by_label": modern_counts(rows),
        "slot_errors": len(slot_errors(rows)),
        "lineage_errors": len(lineage_errors(rows)),
        "duplicate_text_groups": len(duplicate_texts(rows)),
    }
    metrics["min_authored_per_label"] = min(metrics["authored_by_label"].values())
    metrics["min_contrast_pairs_per_label"] = min(metrics["contrast_pairs_by_label"].values())
    metrics["min_rows_per_resolution_type"] = min(metrics["resolution_types"].values())
    return metrics


# ---------------------------------------------------------------- 인프라
def load_rows(path: Path | str = DEFAULT_PATH) -> list[IntentExample]:
    """JSONL을 `IntentExample`로 엄격하게 읽는다(알 수 없는 필드·누락은 예외)."""
    return [from_jsonable(IntentExample, json.loads(line))
            for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- 응용·표현
def main(argv: list[str] | None = None) -> int:
    """집계를 출력하고 `--write <이름>`이면 결과 JSON(`docs/evidence/perf/E-06-02/<이름>.json`)을 쓴다."""
    parser = argparse.ArgumentParser(description="의도 예시 데이터 집계·검사(E-06-02)")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    parser.add_argument("--write", metavar="NAME", help="결과 JSON 이름")
    args = parser.parse_args(argv)
    metrics = summarize(load_rows(args.path))
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if args.write:
        from tools.evidence_result import write_result
        print(write_result("E-06-02", args.write, metrics, "python -m tools.intent_examples"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
