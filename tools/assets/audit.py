"""에셋 정식 검사 규칙(도메인, E-08-07). 파일·OS를 건드리지 않는 순수 함수만 둔다.

- `parse_sources_md`: `content/images/SOURCES.md` 본문 → 파일 경로별 생성 기록 행(에셋2)
- `check_records`: manifest의 실제 파일(`placeholder: false`)마다 생성 기록 행과 필수 칸이 있는지(에셋2)
- `check_placeholder_policy`: 기본 모드에서 임시 파일을 허용한 유형 밖의 `placeholder: true` 항목(7번)
- `check_map_regions`: 지도 항목의 장소 안내 좌표(6번)

에셋3(형식·크기·용량·파일명·저장 경로)과 에셋4(콘텐츠 ID와 1:1, 누락·남는 파일)는 `spec.reconcile`과
`make_placeholders.inspect_files`가 이미 같은 규칙으로 판정하므로 여기서 다시 쓰지 않는다(`check_assets`가 함께 부른다).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

from tools.assets.spec import KINDS, AssetEntry, key_label

DISCLAIMER = "비상업 교육 프로젝트용 생성물"
# 에셋2 필수 칸: (이름, 머리 칸에 들어 있으면 그 칸으로 보는 낱말들, 그중 이 낱말과 함께 쓰인 "프롬프트"는 제외).
# 한 칸이 둘을 채울 수 있다(예: "입력·금지 지시"). "부정 프롬프트" 칸은 프롬프트 전문으로 치지 않는다.
REQUIRED_FIELDS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("도구", ("도구",), ()),
    ("모델·버전", ("모델",), ()),
    ("프롬프트 전문", ("프롬프트", "입력"), ("부정",)),
    ("부정 프롬프트", ("부정", "금지"), ()),
    ("시드", ("시드",), ()),
    ("생성일", ("생성",), ()),
)
EMPTY_CELLS = ("", "-", "—", "없음")
REFERENCE_WORDS = ("기준", "입력", "참고", "원본")   # 이 낱말이 든 "파일" 칸은 입력·기준 그림 칸이라 결과 파일로 보지 않는다
_PATH_RE = re.compile(r"`([^`]+)`")


@dataclass
class SourcesIndex:
    """생성 기록 문서에서 읽은 값. `rows`는 파일 경로 → 그 행의 {머리 칸: 값}."""
    has_disclaimer: bool = False
    rows: dict[str, dict[str, str]] = field(default_factory=dict)
    duplicates: list[str] = field(default_factory=list)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _output_columns(header: list[str]) -> list[int]:
    """결과 파일 칸 번호: 머리에 "파일"이 있고 입력·기준 그림 낱말이 없는 칸. 없으면 빈 목록(그 표는 색인하지 않음)."""
    return [j for j, h in enumerate(header) if "파일" in h and not any(w in h for w in REFERENCE_WORDS)]


def parse_sources_md(text: str, root: str) -> SourcesIndex:
    """표마다 머리 줄을 읽고, 결과 파일 칸의 `` `<root>/…` `` 경로를 그 행의 키로 둔다(한 경로가 두 행에 있으면 `duplicates`).

    표정 sprite처럼 다른 에셋을 편집 입력·기준 그림으로 적은 칸은 결과로 보지 않는다(그 에셋의 기록을 덮어쓰지 않게).

    머리말(첫 번째 비어 있지 않은 줄)에 "비상업 교육 프로젝트용 생성물"이 있어야 한다(11/5 PM 결정).
    """
    idx = SourcesIndex()
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    rest = first.replace(DISCLAIMER, "").strip(" \t#·-—|:,()")
    idx.has_disclaimer = DISCLAIMER in first and bool(rest)    # 문구와 함께 도구 이름(문구 밖의 글자)이 있어야 한다
    prefix = root.rstrip("/") + "/"
    header: Optional[list[str]] = None
    for i, line in enumerate(lines):
        if not line.startswith("|"):
            header = None
            continue
        cells = _cells(line)
        if header is None:
            header = cells
            continue
        if all(set(c) <= set("-: ") for c in cells):
            continue                        # 구분 줄
        row = {h: (cells[j] if j < len(cells) else "") for j, h in enumerate(header)}
        for j in _output_columns(header):
            for path in _PATH_RE.findall(cells[j] if j < len(cells) else ""):
                if not path.startswith(prefix):
                    continue
                if path in idx.rows:
                    idx.duplicates.append(path)
                idx.rows[path] = row
    return idx


def _header_matches(h: str, words: tuple[str, ...], excludes: tuple[str, ...]) -> bool:
    """머리 칸 `h`가 필수 칸에 해당하는지. `excludes` 낱말이 있으면 "입력"처럼 다른 낱말로만 해당할 수 있다."""
    if any(x in h for x in excludes):
        return any(w in h for w in words if w != "프롬프트")
    return any(w in h for w in words)


def _field_filled(row: dict[str, str], words: tuple[str, ...], excludes: tuple[str, ...] = ()) -> bool:
    return any(_header_matches(h, words, excludes) and v.strip() not in EMPTY_CELLS for h, v in row.items())


def check_records(entries: Iterable[AssetEntry], sources: Optional[SourcesIndex]) -> list[str]:
    """에셋2: 실제 파일마다 생성 기록 행이 있고 필수 칸 6개가 비어 있지 않은지.

    파생 초상(`derived_from`)은 sprite를 잘라 만든 파일이라 원본 sprite의 기록으로 갈음하고, 원본 기록이 있는지만 본다.
    임시 파일(`placeholder: true`)은 면제한다(E-05-05 증빙에 생성 명령).
    """
    entries = list(entries)
    real = [e for e in entries if not e.placeholder]
    if not real:
        return []
    if sources is None:
        return [f"생성 기록 문서 없음: 실제 파일 {len(real)}개의 기록 행이 필요하다"]
    out: list[str] = []
    if not sources.has_disclaimer:
        out.append(f"생성 기록 머리말(첫 줄)에 도구 이름과 '{DISCLAIMER}'가 없음")
    out += [f"{p}: 생성 기록 행이 둘 이상" for p in sorted(set(sources.duplicates))]
    by_label = {key_label(e.key): e for e in entries}
    for e in real:
        target = e
        if e.derived_from is not None:
            src = by_label.get(e.derived_from)
            if src is None:
                continue                    # 원본 없음은 쌍 검사(pair_mismatch)가 보고한다
            target = src
        row = sources.rows.get(target.path)
        if row is None:
            out.append(f"{e.path}: 생성 기록 행 없음" + (f"(원본 {target.path})" if target is not e else ""))
            continue
        if target is not e:
            continue                        # 원본 행의 칸은 원본 항목에서 본다
        missing = [name for name, words, excludes in REQUIRED_FIELDS if not _field_filled(row, words, excludes)]
        if missing:
            out.append(f"{e.path}: 생성 기록 칸 비었음({', '.join(missing)})")
    return sorted(out)


def parse_allowed(text: Optional[str]) -> frozenset[str]:
    """`place,ui,map,ad` → 유형 집합. 모르는 유형이면 ValueError."""
    if not text:
        return frozenset()
    kinds = frozenset(k.strip() for k in text.split(",") if k.strip())
    bad = sorted(kinds - set(KINDS))
    if bad:
        raise ValueError(f"알 수 없는 유형: {bad} (가능: {', '.join(KINDS)})")
    return kinds


def check_placeholder_policy(entries: Iterable[AssetEntry], placeholder_mode: bool,
                             allowed: frozenset[str]) -> list[str]:
    """임시 파일 허용 범위. `--placeholder`(임시 세트)면 모든 유형 허용, 기본이면 `allowed` 유형만 허용.

    어느 모드든 실제 파일(`placeholder: false`)의 검사는 그대로 한다(이 함수는 임시 표시만 본다).
    """
    if placeholder_mode:
        return []
    return sorted(f"{key_label(e.key)}: 임시 파일(placeholder: true)인데 실제 파일이 필요한 유형"
                  for e in entries if e.placeholder and e.kind not in allowed)


def check_map_regions(entries: Iterable[AssetEntry], place_ids: Iterable[str]) -> list[str]:
    """지도 항목마다 `regions`가 장소 id 전부를 정확히 한 번씩, 지도 안의 겹치지 않는 양수 크기 상자로 갖는지."""
    want = list(place_ids)
    out: list[str] = []
    for e in entries:
        if e.kind != "map":
            continue
        label = key_label(e.key)
        if not e.regions:
            out.append(f"{label}: 장소 좌표(regions) 없음")
            continue
        ids = [pid for pid, _ in e.regions]
        if sorted(ids) != sorted(want) or len(set(ids)) != len(ids):
            out.append(f"{label}: 장소 좌표 id {ids} ≠ 장소 {want}")
        boxes = []
        for pid, (x, y, w, h) in e.regions:
            if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > e.width or y + h > e.height:
                out.append(f"{label}: {pid} 상자 {[x, y, w, h]}가 지도 {e.width}x{e.height} 밖이거나 형식 오류")
                continue
            for qid, (qx, qy, qw, qh) in boxes:
                if x < qx + qw and qx < x + w and y < qy + qh and qy < y + h:
                    out.append(f"{label}: {pid}와 {qid} 상자가 겹침")
            boxes.append((pid, (x, y, w, h)))
    return out
