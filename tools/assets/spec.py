"""에셋 규격과 기대 항목 계산(도메인). 파일·OS를 건드리지 않는 순수 함수만 둔다.

- `parse_spec_md`: `docs/assets/spec.md` 본문 → `AssetSpec`
- `expected_entries`: 규격 + 콘텐츠 ID 목록 → manifest 기대 항목
- `reconcile`: 기대 항목 ↔ manifest ↔ 디스크 파일 대조(유형별 수, 누락, 남는 것, 초상-sprite 쌍 상태)
- `map_regions`: 임시 지도 위 장소 아이콘 안내 좌표(정보 없는 균등 배치, E-08-07 6번)

초상(portrait)은 같은 id의 sprite에서 인물별 크롭 위 y(`vn.portrait_top`)부터 512×512를 잘라 만든다(`derived_from: "sprite:<id>"`, 11/5 크로스체크 17).
표정 변형(11/5 PM 결정, 크로스체크 19)은 id 접미사 `<인물 id>__<표정 id>`인 sprite·초상 항목이다. 평상시는 접미사 없는
기본 항목 그대로다. 표정 목록과 대상 인물은 규격표 8절에서 읽는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Callable, Iterable, Optional

KINDS: tuple[str, ...] = ("portrait", "sprite", "background", "mascot", "place", "ui", "map", "ad")
MUDANG_VARIANT_IDS: tuple[str, ...] = ("mudang_f", "mudang_m")  # 주인공 여·남(11/4 PM 결정, B-08-02 mudang_variant)
MAP_IDS: tuple[str, ...] = ("village",)
BACKGROUND_IDS: tuple[str, ...] = ("meeting", "night")  # 회의 장소 고정 1장, 밤 마을 공통 1장(spec.md 7절)
MASCOT_IDS: tuple[str, ...] = ("jokjebi", "kkachi")     # 족제비(무당의 짝, 안내)·까치(아침 소식), 11/5 PM 결정 P-21
VARIANT_SEP = "__"                                      # 표정 변형 id = <인물 id>__<표정 id>
AD_URL_PREFIX = "/static/ads/"
SYNTHETIC_CAMPAIGN_PREFIX = "syn:"
ID_RE = re.compile(r"^[a-z0-9_]+$")
SIZE_RE = re.compile(r"^(\d+)\s*[×x]\s*(\d+)$")


class SpecError(ValueError):
    """규격표·입력이 규칙과 맞지 않는다(표 누락, 잘못된 크기, 경로 충돌 등)."""


@dataclass(frozen=True)
class AssetSpec:
    """규격표에서 읽은 값. `sizes`는 광고를 뺀 kind → (가로, 세로)."""
    image_format: str
    max_bytes: int
    root: str
    manifest_path: str
    sizes: dict[str, tuple[int, int]]
    ui_icon_ids: tuple[str, ...]
    kind_max_bytes: dict[str, int] = field(default_factory=dict)
    expressions: tuple[tuple[str, str], ...] = ()      # 8절 (표정 id, 이름). 평상시(기본)는 넣지 않는다
    expression_targets: tuple[str, ...] = ()           # 8절 표정 변형을 만드는 인물 id

    def max_bytes_for(self, kind: str) -> int:
        """kind별 용량 상한. 1절에 `용량 상한(바이트) <kind>` 행이 없으면 공통 상한."""
        return self.kind_max_bytes.get(kind, self.max_bytes)


@dataclass(frozen=True)
class AssetEntry:
    """manifest 항목 1개. 키는 (kind, id, size). size는 광고만, derived_from은 파생 초상만 있다."""
    kind: str
    id: str
    path: str
    url: str
    width: int
    height: int
    size: Optional[tuple[int, int]] = None
    placeholder: bool = True
    derived_from: Optional[str] = None
    edited_from: Optional[str] = None      # 표정 변형 sprite의 기본 sprite(`sprite:<인물 id>`). 얼굴만 편집해 만든다
    edited_from_sha256: Optional[str] = None  # 실제 표정 sprite를 만들 때 쓴 기본 sprite 파일의 SHA-256(기본 교체 감지)
    regions: Optional[tuple[tuple[str, tuple[int, int, int, int]], ...]] = None  # 지도만. (장소 id, (x, y, 가로, 세로))

    @property
    def key(self) -> tuple[str, str, Optional[tuple[int, int]]]:
        """manifest 유일 키 (kind, id, size)."""
        return (self.kind, self.id, self.size)

    def to_json(self) -> dict:
        """manifest.json 한 항목(spec.md 4절 형식)."""
        d: dict = {"kind": self.kind, "id": self.id}
        if self.size is not None:
            d["size"] = [self.size[0], self.size[1]]
        d.update({"path": self.path, "url": self.url, "placeholder": self.placeholder})
        if self.derived_from is not None:
            d["derived_from"] = self.derived_from
        if self.edited_from is not None:
            d["edited_from"] = self.edited_from
        if self.edited_from_sha256 is not None:
            d["edited_from_sha256"] = self.edited_from_sha256
        if self.regions is not None:
            d["regions"] = {pid: list(box) for pid, box in self.regions}
        return d


def derived_source(kind: str, cid: str) -> Optional[str]:
    """파생 항목의 원본 키 문자열. 초상은 같은 id의 sprite(`sprite:<id>`), 그 밖은 None."""
    return f"sprite:{cid}" if kind == "portrait" else None


def sort_key(entry: AssetEntry):
    """manifest 정렬 순서: kind(KINDS 순서) → id → size."""
    return (KINDS.index(entry.kind), entry.id, entry.size or (0, 0))


# ---------------------------------------------------------------- spec.md 해석
def _table_after(text: str, heading: str) -> list[list[str]]:
    """`## <번호>. <heading>` 아래 첫 표의 데이터 행(머리·구분 줄 제외)을 칸 목록으로 돌려준다."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith("## ") and line.rstrip().endswith(heading):
            start = i + 1
            break
    if start is None:
        raise SpecError(f"spec.md에 '{heading}' 절이 없음")
    rows: list[list[str]] = []
    in_table = False
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("|"):
            in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows.append(cells)
        elif in_table:
            break
    if len(rows) < 3:
        raise SpecError(f"'{heading}' 절의 표가 비었음")
    return rows[2:]


def _strip_code(cell: str) -> str:
    return cell.strip().strip("`").strip()


def parse_spec_md(text: str) -> AssetSpec:
    """규격표 본문을 읽는다. 1절(공통 규칙), 2절(유형별 규격), 3절(UI 아이콘 목록)."""
    common = {r[0]: _strip_code(r[1]) for r in _table_after(text, "공통 규칙") if len(r) >= 2}
    for need in ("형식", "용량 상한(바이트)", "저장 루트", "manifest"):
        if need not in common:
            raise SpecError(f"공통 규칙 표에 '{need}' 행이 없음")
    if not common["용량 상한(바이트)"].isdigit():
        raise SpecError(f"용량 상한이 정수가 아님: {common['용량 상한(바이트)']!r}")

    kind_max: dict[str, int] = {}
    for key, val in common.items():
        if key.startswith("용량 상한(바이트) "):
            k = _strip_code(key[len("용량 상한(바이트) "):])
            if k not in KINDS or not val.isdigit():
                raise SpecError(f"kind별 용량 상한 행 오류: {key!r} = {val!r}")
            kind_max[k] = int(val)

    sizes: dict[str, tuple[int, int]] = {}
    kinds_seen: list[str] = []
    for row in _table_after(text, "유형별 규격"):
        kind = _strip_code(row[0])
        if kind not in KINDS:
            raise SpecError(f"알 수 없는 kind: {kind!r}")
        kinds_seen.append(kind)
        if kind == "ad":
            continue                       # 광고 크기는 계약 SLOT_SIZES를 그대로 쓴다
        m = SIZE_RE.match(_strip_code(row[2]))
        if not m:
            raise SpecError(f"{kind} 크기를 읽을 수 없음: {row[2]!r}")
        sizes[kind] = (int(m.group(1)), int(m.group(2)))
    if sorted(kinds_seen) != sorted(KINDS):
        raise SpecError(f"유형별 규격 표의 kind가 {KINDS}와 다름: {kinds_seen}")

    icons = tuple(_strip_code(r[0]) for r in _table_after(text, "UI 아이콘 목록"))
    bad = [i for i in icons if not ID_RE.match(i)]
    if bad:
        raise SpecError(f"아이콘 ID 형식 오류(영문 소문자·숫자·_): {bad}")
    if len(set(icons)) != len(icons):
        raise SpecError(f"아이콘 ID 중복: {icons}")

    expressions: list[tuple[str, str]] = []
    targets: list[str] = []
    if "## 8." in text:
        for r in _table_after(text, "표정 목록"):
            eid, label = _strip_code(r[0]), _strip_code(r[1])
            if not ID_RE.match(eid) or VARIANT_SEP in eid:
                raise SpecError(f"표정 ID 형식 오류: {eid!r}")
            expressions.append((eid, label))
        targets = [_strip_code(r[0]) for r in _table_after(text, "표정 대상")]
        if len({e for e, _ in expressions}) != len(expressions) or len(set(targets)) != len(targets):
            raise SpecError(f"표정 ID·대상 중복: {expressions} / {targets}")
        bad_t = [t for t in targets if not ID_RE.match(t) or VARIANT_SEP in t]
        if bad_t:
            raise SpecError(f"표정 대상 ID 형식 오류: {bad_t}")

    return AssetSpec(
        image_format=common["형식"].lower(),
        max_bytes=int(common["용량 상한(바이트)"]),
        root=common["저장 루트"].rstrip("/"),
        manifest_path=common["manifest"],
        sizes=sizes,
        ui_icon_ids=icons,
        kind_max_bytes=kind_max,
        expressions=tuple(expressions),
        expression_targets=tuple(targets),
    )


def split_variant(cid: str) -> tuple[str, Optional[str]]:
    """`hunjang__joy` → ("hunjang", "joy"), `hunjang` → ("hunjang", None)."""
    base, sep, expr = cid.partition(VARIANT_SEP)
    return (base, expr) if sep else (cid, None)


def variant_id(cid: str, expr: str) -> str:
    """인물 id + 표정 id → 표정 변형 id."""
    return f"{cid}{VARIANT_SEP}{expr}"


# ---------------------------------------------------------------- 기대 항목
def _simple_entry(spec: AssetSpec, kind: str, cid: str) -> AssetEntry:
    if not ID_RE.match(cid):
        raise SpecError(f"{kind} ID 형식 오류: {cid!r}")
    w, h = spec.sizes[kind]
    ext = spec.image_format
    return AssetEntry(kind=kind, id=cid, path=f"{spec.root}/{kind}/{cid}.{ext}",
                      url=f"/static/assets/{kind}/{cid}.{ext}", width=w, height=h)


def ad_entries(spec: AssetSpec, campaigns: Iterable[dict],
               slot_sizes: dict[str, tuple[int, int]],
               slot_image_path: Callable[[str, str], str]) -> list[AssetEntry]:
    """소재마다 그 캠페인 지면의 규격만(중복 제거한 (creative_id, 규격)). 캠페인 상태는 보지 않는다."""
    out: dict[tuple[str, tuple[int, int]], AssetEntry] = {}
    for cp in campaigns:
        if str(cp.get("campaign_id", "")).startswith(SYNTHETIC_CAMPAIGN_PREFIX):
            continue                       # 합성 캠페인은 같은 파일을 가리킨다
        for slot in cp["slots"]:
            if slot not in slot_sizes:
                raise SpecError(f"{cp['campaign_id']}: 알 수 없는 지면 {slot!r}")
            size = tuple(slot_sizes[slot])
            for cr in cp["creatives"]:
                key = (cr["creative_id"], size)
                if key in out:
                    continue
                url = slot_image_path(cr["image_path"], slot)
                if not url.startswith(AD_URL_PREFIX) or "/" in url[len(AD_URL_PREFIX):]:
                    raise SpecError(f"{cr['creative_id']}: 광고 url이 {AD_URL_PREFIX}<파일명> 형식이 아님: {url!r}")
                name = url[len(AD_URL_PREFIX):]
                if not name.endswith("." + spec.image_format):
                    raise SpecError(f"{cr['creative_id']}: 광고 파일 형식이 {spec.image_format}가 아님: {url!r}")
                out[key] = AssetEntry(kind="ad", id=cr["creative_id"], size=size,
                                      path=f"{spec.root}/ad/{name}", url=url,
                                      width=size[0], height=size[1])
    return list(out.values())


def map_regions(place_ids: Iterable[str], map_size: tuple[int, int],
                icon_size: tuple[int, int]) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    """임시 지도의 장소 아이콘 안내 좌표. 장소 순서대로 가로 한 줄 균등 칸의 가운데, 세로 가운데에 아이콘 크기 상자.

    위치에 게임 정보가 없다(장소 사이 거리·방향을 뜻하지 않는다, spec.md 2절). B-10-02가 아이콘을 놓을 자리다.
    """
    ids = list(place_ids)
    if not ids:
        return ()
    (mw, mh), (iw, ih) = map_size, icon_size
    cell = mw // len(ids)
    if cell < iw or mh < ih:
        raise SpecError(f"지도 {mw}x{mh}에 장소 {len(ids)}개의 {iw}x{ih} 아이콘이 들어가지 않음")
    y = (mh - ih) // 2
    return tuple((pid, (i * cell + (cell - iw) // 2, y, iw, ih)) for i, pid in enumerate(ids))


def expected_entries(spec: AssetSpec, npc_ids: Iterable[str], place_ids: Iterable[str],
                     campaigns: Iterable[dict], slot_sizes: dict[str, tuple[int, int]],
                     slot_image_path: Callable[[str, str], str]) -> list[AssetEntry]:
    """규격 + 콘텐츠 ID 목록 → 정렬된 기대 항목. 키·경로·url 충돌이 있으면 SpecError."""
    entries: list[AssetEntry] = []
    place_ids = list(place_ids)
    people = list(npc_ids) + list(MUDANG_VARIANT_IDS)
    unknown = [t for t in spec.expression_targets if t not in people]
    if unknown:
        raise SpecError(f"표정 대상이 인물 목록에 없음: {unknown}")
    people += [variant_id(t, ex) for t in spec.expression_targets for ex, _ in spec.expressions]
    for cid in people:
        e = _simple_entry(spec, "portrait", cid)
        entries.append(replace(e, derived_from=derived_source("portrait", cid)))
    for cid in people:
        base, expr = split_variant(cid)
        e = _simple_entry(spec, "sprite", cid)
        entries.append(replace(e, edited_from=f"sprite:{base}") if expr else e)
    entries += [_simple_entry(spec, "background", b) for b in BACKGROUND_IDS]
    entries += [_simple_entry(spec, "mascot", m) for m in MASCOT_IDS]
    entries += [_simple_entry(spec, "place", p) for p in place_ids]
    entries += [_simple_entry(spec, "ui", i) for i in spec.ui_icon_ids]
    regions = map_regions(place_ids, spec.sizes["map"], spec.sizes["place"])
    entries += [replace(_simple_entry(spec, "map", m), regions=regions) for m in MAP_IDS]
    entries += ad_entries(spec, campaigns, slot_sizes, slot_image_path)
    for attr in ("key", "path", "url"):
        seen: set = set()
        for e in entries:
            v = getattr(e, attr)
            if v in seen:
                raise SpecError(f"{attr} 중복: {v!r}")
            seen.add(v)
    return sorted(entries, key=sort_key)


# ---------------------------------------------------------------- 대조
@dataclass
class Reconciliation:
    """유형별 기대 수·manifest 항목 수·파일 수와 누락·남는 목록."""
    expected: dict[str, int] = field(default_factory=dict)
    manifest: dict[str, int] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=dict)
    missing_in_manifest: list[str] = field(default_factory=list)
    extra_in_manifest: list[str] = field(default_factory=list)
    manifest_mismatch: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)
    pair_mismatch: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """유형별 세 수가 같고 누락·남는 것이 0건이면 True."""
        same = all(self.expected.get(k, 0) == self.manifest.get(k, 0) == self.files.get(k, 0) for k in KINDS)
        return same and not (self.missing_in_manifest or self.extra_in_manifest or self.manifest_mismatch
                             or self.missing_files or self.extra_files or self.pair_mismatch)


def key_label(key) -> str:
    """키를 사람이 읽는 문자열로(`ad:cr_gat@300x250`, `portrait:hunjang`)."""
    kind, cid, size = key
    return f"{kind}:{cid}" + (f"@{size[0]}x{size[1]}" if size else "")


def kind_of_path(path: str, root: str) -> Optional[str]:
    """`<root>/<kind>/<파일>` 경로의 kind. 형식이 다르면 None."""
    prefix = root.rstrip("/") + "/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix):].split("/")
    if len(parts) != 2 or parts[0] not in KINDS:
        return None
    return parts[0]


def reconcile(expected: list[AssetEntry], manifest: list[AssetEntry],
              files_on_disk: Iterable[str], root: str) -> Reconciliation:
    """기대 항목 ↔ manifest ↔ 디스크 파일(`<root>/<kind>/…` 경로 목록)을 대조한다."""
    r = Reconciliation()
    files = sorted(set(files_on_disk))
    for k in KINDS:
        r.expected[k] = sum(1 for e in expected if e.kind == k)
        r.manifest[k] = sum(1 for e in manifest if e.kind == k)
        r.files[k] = sum(1 for f in files if kind_of_path(f, root) == k)
    exp_keys = {e.key: e for e in expected}
    man_keys = {e.key: e for e in manifest}
    r.missing_in_manifest = sorted(key_label(k) for k in exp_keys.keys() - man_keys.keys())
    r.extra_in_manifest = sorted(key_label(k) for k in man_keys.keys() - exp_keys.keys())
    r.manifest_mismatch = sorted(
        f"{key_label(k)}: path/url {man_keys[k].path} {man_keys[k].url}"
        for k in exp_keys.keys() & man_keys.keys()
        if (man_keys[k].path, man_keys[k].url) != (exp_keys[k].path, exp_keys[k].url))
    r.manifest_mismatch += sorted(
        f"{key_label(k)}: derived_from {man_keys[k].derived_from!r} ≠ {exp_keys[k].derived_from!r}"
        for k in exp_keys.keys() & man_keys.keys()
        if man_keys[k].derived_from != exp_keys[k].derived_from)
    r.manifest_mismatch += sorted(
        f"{key_label(k)}: edited_from {man_keys[k].edited_from!r} ≠ {exp_keys[k].edited_from!r}"
        for k in exp_keys.keys() & man_keys.keys()
        if man_keys[k].edited_from != exp_keys[k].edited_from)
    r.pair_mismatch = pair_mismatches(manifest)
    exp_paths = {e.path for e in expected}
    file_set = set(files)
    r.missing_files = sorted(exp_paths - file_set)
    r.extra_files = sorted(f for f in file_set - exp_paths if kind_of_path(f, root) is not None)
    return r


def pair_mismatches(manifest: list[AssetEntry]) -> list[str]:
    """파생 항목과 원본의 `placeholder`가 다르거나 원본이 없으면 목록에 넣는다.

    초상은 sprite에서 잘라 만들므로 한 쌍은 함께 임시이거나 함께 실제다(크로스체크 17 지적 6).
    """
    by_label = {key_label(e.key): e for e in manifest}
    out = []
    for e in manifest:
        if e.derived_from is None:
            continue
        src = by_label.get(e.derived_from)
        if src is None:
            out.append(f"{key_label(e.key)}: 원본 {e.derived_from} 없음")
        elif src.placeholder != e.placeholder:
            out.append(f"{key_label(e.key)}: placeholder {str(e.placeholder).lower()} ≠ "
                       f"{e.derived_from} {str(src.placeholder).lower()}")
    return sorted(out)
