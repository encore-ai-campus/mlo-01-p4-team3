"""A-09-05: 이미 바탕이 투명한 원본(RGBA)을 규격 sprite·마스코트로 맞춰 실제 경로에 쓴다.

    python -m tools.assets.normalize <원본 폴더> [--repo <저장소>] [--report <JSON 경로>]

- 원본 폴더 구조: `characters/<인물 id>.png`(sprite), `mascots/<MASCOT_SOURCES 파일명>`(마스코트).
  원본 폴더는 읽기만 한다. 바탕 제거가 필요한 단색 바탕 원본은 `cutout`을 쓴다.
- sprite: 인물별 머리카락 윗선(`HAIR_TOP`, 원본 좌표, 사람이 확인한 값)과 보이는 발끝으로 단일 배율을 정해
  키 등급 머리 꼭대기·발끝 y=1248·가로 가운데 256에 놓는다(`cutout.fit_sprite`, 가로·세로 같은 배율).
- 마스코트: 보이는 경계 상자를 단일 배율로 줄여 512×512 가운데, 발끝 기준선 y=480, 위 여백 `MASCOT_TOP` 이상에 둔다.
- 쓴 sprite·마스코트 항목은 manifest `placeholder: false`로 바꾸고, 그 sprite에서 파생한 초상은 `placeholder: true`로 돌린다.
  초상은 이어서 `python -m tools.assets.make_placeholders`로 다시 파생한다(이 도구는 초상 파일을 쓰지 않는다).
- 같은 입력이면 같은 픽셀이 나온다(면적 평균 축소, 난수 없음). 압축 바이트는 zlib 구현에 따라 다를 수 있다(spec 5절).

계층: `fit_mascot`·`HAIR_TOP`은 규칙(도메인, 파일 없음), `normalize`는 흐름(응용), `main`은 CLI(표현).
파일 읽기·쓰기는 `png`·`sources`(인프라).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from tools.assets import png, sources, vn
from tools.assets.cutout import fit_sprite

# 인물 id → 원본(793×1983) 머리카락 윗선 y. 장식(갓·상투·뻗친 머리 끝 몇 가닥)은 넣지 않는다(spec 6절).
# 갓을 쓴 인물은 머리카락이 갓에 가려 알파로 찾을 수 없다. 갓 없는 5명의 비율
# (눈 − 머리카락 윗선) / (턱 − 눈) 평균 1.23(주모 1.31, 과부 1.24,머슴 1.20, 약초꾼 1.16, 대장장이 1.22)을
# 갓 쓴 인물의 눈·턱에 적용해 추정하고, 갓 아래로 보이는 머리카락 위치와 어긋나지 않는지 눈으로 확인했다.
# 눈 y는 눈동자 가운데, 턱 y는 턱 끝(수염 아래 턱선)이다. 11/9 E가 2배 확대 그림에서 확인.
HAIR_TOP: dict[str, tuple[int, str]] = {
    "mudang_f": (287, "갓. 눈 395·턱 481 → 추정 289. 갓 챙 아래 가르마가 보이기 시작하는 287을 쓴다"),
    "mudang_m": (129, "갓. 눈 266·턱 377 → 추정 129. 갓 챙 아래 보이는 머리카락 187보다 위(갓 속)"),
    "hunjang": (155, "갓. 눈 300·턱선 418(수염 위) → 추정 155. 망건 윗선 230보다 위(갓 속 상투 아래)"),
    "jumo": (55, "쪽머리 윗선이 그대로 보임(눈 225·턱 355)"),
    "gwabu": (147, "쪽머리 윗선이 그대로 보임(눈 302·턱 427)"),
    "meosum": (127, "상투(위 72)를 뺀 머리카락 윗선(눈 276·턱 400)"),
    "yakcho": (122, "상투(위 80)를 뺀 머리카락 윗선(눈 275·턱 407)"),
    "blacksmith": (62, "뻗친 머리 끝 몇 가닥(위 54~57) 아래 머리카락 덩어리 윗선(눈 230·턱 357)"),
}
# 마스코트 id → 원본 파일명(공유 폴더 `mascots/`).
MASCOT_SOURCES = {"kkachi": "kkachi-v2.png", "jokjebi": "jokjebi-v2.png"}
MASCOT_FOOT_Y = vn.MASCOT_SIZE[1] - sum(vn.BOTTOM_MARGIN) // 2      # 480
MASCOT_TOP = 32                                                       # 마스코트 위 최소 여백(px)
MASCOT_SIDE = 16                                                      # 마스코트 좌우 최소 여백(px)


def fit_mascot(width: int, rows: Sequence[bytes]) -> list[bytearray]:
    """RGBA 마스코트를 단일 배율로 줄여 512×512 가운데, 보이는 아랫변 y=480에 둔다. 확대는 하지 않는다."""
    if width < 1 or not rows or any(len(r) != width * 4 for r in rows):
        raise ValueError("RGBA 행 길이가 맞지 않음")
    box = vn.visible_box(width, rows)
    if box is None:
        raise ValueError("보이는 전경 없음")
    x0, y0, x1, y1 = box
    size_w, size_h = vn.MASCOT_SIZE
    scale = min(1.0, (MASCOT_FOOT_Y - MASCOT_TOP) / (y1 - y0), (size_w - 2 * MASCOT_SIDE) / (x1 - x0))
    nw, nh = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    cropped = [r[x0 * 4:x1 * 4] for r in rows[y0:y1]]
    small = vn.resize_rgba(cropped, x1 - x0, (nw, nh))
    out = [bytearray(size_w * 4) for _ in range(size_h)]
    tx, ty = (size_w - nw) // 2, MASCOT_FOOT_Y - nh
    for y, line in enumerate(small):
        out[ty + y][tx * 4:(tx + nw) * 4] = line
    return out


def _encode(width: int, height: int, rows: list, kind: str) -> bytes:
    """무손실 PNG. 용량 상한을 넘으면 행별 필터 선택으로 다시 압축하고, 그래도 넘으면 ValueError."""
    limit = sources.load_spec().max_bytes_for(kind)
    data = png.encode_rgba(width, height, rows)
    if len(data) > limit:
        data = png.encode_rgba(width, height, rows, optimize=True)
    if len(data) > limit:
        raise ValueError(f"{kind}: 무손실 재압축 후에도 {len(data)} > {limit}바이트")
    return data


def _sprite_metrics(cid: str, src_w: int, src_rows: list, out_rows: list) -> dict:
    sx0, sy0, sx1, sy1 = vn.visible_box(src_w, src_rows)
    head = HAIR_TOP[cid][0]
    grade = vn.grade_for(cid)
    scale = (vn.FOOT_Y - grade.top) / (sy1 - head)
    x0, y0, x1, y1 = vn.visible_box(vn.SPRITE_SIZE[0], out_rows)
    return {"grade": grade.name, "src_box": [sx0, sy0, sx1, sy1], "hair_top_src": head,
            "scale": round(scale, 4), "hair_top_out": round(vn.FOOT_Y - (sy1 - head) * scale, 1),
            "grade_top": grade.top, "out_box": [x0, y0, x1, y1], "foot_y": y1, "center_x": (x0 + x1) / 2}


def normalize(src_dir: Path, repo: Path) -> dict:
    """원본 폴더의 인물·마스코트를 규격대로 맞춰 `repo`에 쓰고 manifest 표시를 바꾼다. 인물별 수치를 돌려준다."""
    spec = sources.load_spec()
    manifest = sources.load_manifest(repo, spec)
    by_key = {(e.kind, e.id): e for e in manifest}
    jobs = [("sprite", cid, src_dir / "characters" / f"{cid}.png") for cid in HAIR_TOP]
    jobs += [("mascot", mid, src_dir / "mascots" / name) for mid, name in MASCOT_SOURCES.items()]
    report: dict = {}
    updated = []
    for kind, cid, path in jobs:
        entry = by_key.get((kind, cid))
        if entry is None:
            raise ValueError(f"manifest에 {kind}:{cid} 항목이 없음")
        w, h, ch, rows = png.decode(path.read_bytes())
        if ch != 4:
            raise ValueError(f"{path.name}: RGBA가 아님(바탕 제거는 cutout)")
        if kind == "sprite":
            out = fit_sprite(w, rows, cid, HAIR_TOP[cid][0])
            problems = vn.check_sprite(*vn.SPRITE_SIZE, 4, out)
            metrics = _sprite_metrics(cid, w, rows, out)
        else:
            out = fit_mascot(w, rows)
            problems = vn.check_mascot(*vn.MASCOT_SIZE, 4, out)
            x0, y0, x1, y1 = vn.visible_box(vn.MASCOT_SIZE[0], out)
            metrics = {"src_box": list(vn.visible_box(w, rows)), "out_box": [x0, y0, x1, y1],
                       "foot_y": y1, "center_x": (x0 + x1) / 2}
        if problems:
            raise ValueError(f"{kind}:{cid}: " + "; ".join(problems))
        size = vn.SPRITE_SIZE if kind == "sprite" else vn.MASCOT_SIZE
        data = _encode(size[0], size[1], out, kind)
        metrics["bytes"] = len(data)
        report[f"{kind}:{cid}"] = metrics
        updated.append((replace(entry, placeholder=False), data))
    for entry, data in updated:             # 모두 규격을 통과한 뒤에만 쓴다(일부만 바뀐 상태를 남기지 않음)
        sources.write_file(repo, entry.path, data)
    updated = [e for e, _ in updated]
    sprites = {f"sprite:{e.id}" for e in updated if e.kind == "sprite"}
    # 새 sprite에서 초상을 다시 파생하도록 초상 표시를 임시로 돌린다(make_placeholders가 실제 표시 초상은 보호한다).
    updated += [replace(e, placeholder=True) for e in manifest
                if e.kind == "portrait" and e.derived_from in sprites and not e.placeholder]
    keys = {(e.kind, e.id) for e in updated}
    sources.write_manifest(repo, spec, [e for e in manifest if (e.kind, e.id) not in keys] + updated)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: 성공 0, 입력·규격 위반 2."""
    ap = argparse.ArgumentParser(description="투명 원본 → 규격 sprite·마스코트(A-09-05)")
    ap.add_argument("src", type=Path, help="characters/·mascots/가 있는 원본 폴더(읽기만)")
    ap.add_argument("--repo", type=Path, default=sources.ROOT)
    ap.add_argument("--report", type=Path, help="인물별 수치 JSON을 쓸 경로")
    args = ap.parse_args(argv)
    try:
        report = normalize(args.src, args.repo)
    except (OSError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    body = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(body + "\n", encoding="utf-8")
    print(body)
    print("다음: python -m tools.assets.make_placeholders (초상 파생)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
