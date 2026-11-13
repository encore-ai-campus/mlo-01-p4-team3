"""비주얼 노벨 화면 검수용 합성 PNG(표현 + 응용). 저장소의 실제 파일(임시든 실제든)을 그대로 읽어 겹친다.

    python -m tools.assets.preview_vn --bg meeting --id hunjang --out C:\\temp\\meeting_hunjang.png
    python -m tools.assets.preview_vn --bg meeting --id hunjang --mode full --out out/preview_vn/full.png
    python -m tools.assets.preview_vn --bg night --out out/preview_vn/night.png
    python -m tools.assets.preview_vn --bg meeting --id hunjang --expr sad --mascot kkachi --out out/preview_vn/morning.png

- 1280×720 배경 + sprite 1명 + 대사창 사각형. 모드는 `vn.py`: 평소(`normal`, 머리~허리 0.8배, 아랫변이 대사창 윗변)와
  전신 컷(`full`, 전신 0.5배, 대사창 숨김). 밤에는 sprite·마스코트를 두지 않는다(--id·--mascot을 주면 오류).
- `--expr <표정 id>`는 표정 변형 sprite(`<id>__<표정 id>`), `--mascot <id>`는 무대 왼쪽 마스코트 자리(`vn.MASCOT`).
- 출력은 저장소 밖이나 `.gitignore`된 `out/` 아래만 허용한다(검수용 파일을 커밋하지 않게).
- 계층: 합성 규칙은 `vn.py`(도메인), PNG 읽기·쓰기는 `png.py`(인프라), 경로 조회는 `sources.py`(인프라).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.assets import png, sources, vn
from tools.assets.spec import BACKGROUND_IDS, MASCOT_IDS, variant_id


class PreviewError(ValueError):
    """입력이 배치 규칙과 맞지 않는다(밤 sprite, 없는 파일, 저장소 안 출력 등)."""


def _asset_path(repo: Path, kind: str, cid: str) -> Path:
    spec = sources.load_spec(repo / "docs" / "assets" / "spec.md")
    return repo / spec.root / kind / f"{cid}.{spec.image_format}"


def check_out_path(repo: Path, out: Path) -> Path:
    """출력 경로가 저장소 밖이거나 저장소의 out/ 아래인지 본다. 아니면 PreviewError."""
    target = out.resolve()
    root = repo.resolve()
    if target == root or root in target.parents:
        if (root / "out") not in target.parents:
            raise PreviewError(f"저장소 안에는 out/ 아래로만 쓴다: {out}")
    return target


def build(repo: Path, bg: str, cid: str | None, mode: str = "normal", expr: str | None = None,
          mascot: str | None = None) -> list[bytearray]:
    """배경·sprite·마스코트 파일을 읽어 합성한 RGB 행(1280×720). mode는 `vn.MODES`(normal|full)."""
    if mode not in vn.MODES:
        raise PreviewError(f"알 수 없는 모드 {mode!r} (가능: {', '.join(vn.MODES)})")
    if bg not in BACKGROUND_IDS:
        raise PreviewError(f"알 수 없는 배경 {bg!r} (가능: {', '.join(BACKGROUND_IDS)})")
    if bg == "night" and cid is not None:
        raise PreviewError("밤 장면에는 sprite를 두지 않는다(밤 실행자 노출 금지). --id를 빼고 다시 실행")
    if bg == "night" and mascot is not None:
        raise PreviewError("밤 장면에는 마스코트를 두지 않는다")
    if mascot is not None and mascot not in MASCOT_IDS:
        raise PreviewError(f"알 수 없는 마스코트 {mascot!r} (가능: {', '.join(MASCOT_IDS)})")
    if expr is not None and cid is None:
        raise PreviewError("--expr는 --id와 함께 쓴다")
    bw, bh, bch, brows = _read(_asset_path(repo, "background", bg))
    if (bw, bh) != vn.STAGE:
        raise PreviewError(f"배경 크기 {bw}x{bh} ≠ {vn.STAGE[0]}x{vn.STAGE[1]}")
    sprite = None
    if cid is not None:
        sw, sh, sch, srows = _read(_asset_path(repo, "sprite", variant_id(cid, expr) if expr else cid))
        if (sw, sh) != vn.SPRITE_SIZE or sch != 4:
            raise PreviewError(f"sprite {cid}: {sw}x{sh} 채널 {sch} ≠ {vn.SPRITE_SIZE[0]}x{vn.SPRITE_SIZE[1]} RGBA")
        sprite = srows
    mrows = None
    if mascot is not None:
        mw, mh, mch, mrows = _read(_asset_path(repo, "mascot", mascot))
        if (mw, mh) != vn.MASCOT_SIZE or mch != 4:
            raise PreviewError(f"마스코트 {mascot}: {mw}x{mh} 채널 {mch} ≠ 512x512 RGBA")
    return vn.compose(brows, bch, sprite, vn.MODES[mode], mascot=mrows)


def _read(path: Path) -> tuple[int, int, int, list[bytes]]:
    if not path.is_file():
        raise PreviewError(f"파일 없음: {path}")
    return png.decode(path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 성공 0, 입력 오류 2."""
    ap = argparse.ArgumentParser(description="VN 화면 검수용 합성 PNG(배경 + sprite + 대화창 사각형)")
    ap.add_argument("--repo", type=Path, default=sources.ROOT, help="에셋을 읽을 저장소 루트")
    ap.add_argument("--bg", default="meeting", help="배경 id(meeting|night)")
    ap.add_argument("--id", dest="cid", default=None, help="sprite 인물 id(회의만). 생략하면 회의는 hunjang")
    ap.add_argument("--mode", default="normal", help="sprite 표시 방식: normal(평소) | full(전신 컷)")
    ap.add_argument("--expr", default=None, help="표정 id(spec.md 8절, 예: joy). 생략하면 기본(평상시)")
    ap.add_argument("--mascot", default=None, help="마스코트 id(kkachi|jokjebi, 회의만)")
    ap.add_argument("--out", type=Path, required=True, help="출력 PNG(저장소 밖 또는 out/ 아래)")
    args = ap.parse_args(argv)
    cid = args.cid if args.cid is not None or args.bg == "night" else "hunjang"
    try:
        out = check_out_path(args.repo, args.out)
        rows = build(args.repo, args.bg, cid, args.mode, args.expr, args.mascot)
    except (PreviewError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png.encode_rgb(vn.STAGE[0], vn.STAGE[1], rows))
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
