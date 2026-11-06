"""A-08-04: 대비되는 단색 바탕을 제거하고 straight-alpha sprite로 배치한다.

    python -m tools.assets.cutout raw.png sprite.png --id jumo --head-top 195 --background 00ff00

머리 꼭대기는 원본에서 사람이 확인한 좌표(갓·상투 제외)다. 전경과 바탕이 같은 색인
그림은 분리할 수 없다. 부드러운 경계는 8방향 32px 이내의 불투명 전경색으로 혼합을 역산한다.
복잡한 배경·넓은 반투명 물체의 자동 분할 도구가 아니다. 기존 파일을 덮어쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Sequence

from tools.assets import png, sources, vn


def remove_matte(width: int, height: int, channels: int, rows: Sequence[bytes],
                 background: tuple[int, int, int]) -> list[bytearray]:
    """단색 바탕의 RGB/RGBA → 8비트 straight RGBA. 기존 알파는 새 알파에 곱한다.

    생성 원본의 바탕 잡음을 고려해 채널 차이 32 이하는 투명으로 처리한다. 바탕에서 충분히 멀거나 색상 우세가
    다른 내부 픽셀을 불투명 전경 후보로 삼고, 경계에서 C=aF+(1-a)B를 최소제곱으로 푼다.
    선택한 후보가 실제 전경과 다르면 오차가 남으므로 밝고 어두운 바탕 검수도 필요하다.
    """
    if width < 1 or height < 1 or channels not in (3, 4) or len(rows) != height \
            or any(len(r) != width * channels for r in rows):
        raise ValueError("RGB/RGBA 행 수·길이가 크기와 맞지 않음")
    if len(background) != 3 or any(not isinstance(v, int) or not 0 <= v <= 255 for v in background):
        raise ValueError("바탕색은 0~255 RGB 세 값")
    data = b"".join(rows)
    colors = [tuple(data[i:i + 3]) for i in range(0, len(data), channels)]
    distances = [max(abs(c[k] - background[k]) for k in range(3)) for c in colors]
    key = max(range(3), key=lambda k: background[k])
    others = [k for k in range(3) if k != key]
    chroma = background[key] - max(background[k] for k in others)
    visible = bytearray(d > 32 and (channels == 3 or data[i * channels + 3] > 0)
                        for i, d in enumerate(distances))
    solid = bytearray(len(colors))
    for i, color in enumerate(colors):
        if not visible[i]:
            continue
        x, y = i % width, i // width
        inside = all(visible[ny * width + nx]
                     for ny in range(max(0, y - 1), min(height, y + 2))
                     for nx in range(max(0, x - 1), min(width, x + 2)))
        # 검정 머리카락+초록의 혼합도 바탕과의 거리는 크다. 색 우세까지 확인한다.
        solid[i] = inside and (color[key] <= max(color[k] for k in others) + 16
                                if chroma >= 128 else distances[i] >= 200)
    directions = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
    out = [bytearray(width * 4) for _ in range(height)]
    for i, color in enumerate(colors):
        if not visible[i]:
            continue
        alpha = 1.0
        if not solid[i]:
            x, y = i % width, i // width
            candidates = []
            for dx, dy in directions:
                for step in range(1, 33):
                    nx, ny = x + dx * step, y + dy * step
                    if not (0 <= nx < width and 0 <= ny < height):
                        break
                    index = ny * width + nx
                    if solid[index]:
                        foreground = colors[index]
                        vector = [foreground[k] - background[k] for k in range(3)]
                        norm = sum(v * v for v in vector)
                        a = max(0.0, min(1.0, sum((color[k] - background[k]) * vector[k]
                                                   for k in range(3)) / norm))
                        error = sum((color[k] - (background[k] + a * vector[k])) ** 2 for k in range(3))
                        candidates.append((error, step, a))
                        break
            if not candidates:
                raise ValueError(f"경계 ({x},{y}) 주변 32px에 불투명 전경이 없음: 바탕·전경 대비 확인")
            alpha = min(candidates)[2]
        source_alpha = data[i * channels + 3] if channels == 4 else 255
        a8 = round(alpha * source_alpha)
        if a8 == 0:
            continue
        rgb = [max(0, min(255, round((color[k] - (1 - alpha) * background[k]) / alpha)))
               for k in range(3)]
        if chroma >= 128 and alpha < 1:
            # 전경에 키 색을 쓰지 않는 입력에서 남은 경계의 색 번짐을 억제한다.
            rgb[key] = min(rgb[key], max(rgb[k] for k in others))
        out[i // width][i % width * 4:i % width * 4 + 4] = bytes((*rgb, a8))
    return out


def fit_sprite(width: int, rows: Sequence[bytes], cid: str, head_top: float) -> list[bytearray]:
    """RGBA를 키 등급에 맞춰 단일 배율로 배치한다. 폭·위 여백 초과는 왜곡 대신 오류.

    입력의 머리 꼭대기·보이는 발끝 거리로 배율을 정한다. 출력 정수 크기 반올림은
    축당 0.5px 이하이며 몸 일부만 변형하지 않는다. 등신의 정확성은 사람이 검수한다.
    """
    if width < 1 or not rows or any(len(r) != width * 4 for r in rows):
        raise ValueError("RGBA 행 길이가 맞지 않음")
    box = vn.visible_box(width, rows)
    if box is None:
        raise ValueError("보이는 전경 없음")
    x0, y0, x1, y1 = box
    if not math.isfinite(head_top) or not y0 <= head_top < y1:
        raise ValueError("머리 꼭대기는 보이는 전경의 위~발끝 사이 좌표")
    grade = vn.grade_for(cid)
    scale = (vn.FOOT_Y - grade.top) / (y1 - head_top)
    nw, nh = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    target_y = vn.FOOT_Y - nh
    if nw > vn.SPRITE_SIZE[0] or target_y < vn.TOP_MARGIN_MIN:
        raise ValueError("키 등급으로 비율 유지하면 폭 또는 위 여백 초과: 원본을 다시 생성")
    cropped = [r[x0 * 4:x1 * 4] for r in rows[y0:y1]]
    small = vn.resize_rgba(cropped, x1 - x0, (nw, nh))
    out = [bytearray(vn.SPRITE_SIZE[0] * 4) for _ in range(vn.SPRITE_SIZE[1])]
    target_x = (vn.SPRITE_SIZE[0] - nw) // 2
    for y, line in enumerate(small):
        out[target_y + y][target_x * 4:(target_x + nw) * 4] = line
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI: 성공 0, 잘못된 입력·기존 경로·규격 위반 2. 원본·기존 출력은 보존한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--id", required=True, choices=sorted(vn.CHARACTER_GRADE))
    parser.add_argument("--head-top", required=True, type=float)
    parser.add_argument("--background", default="00ff00", help="바탕 RGB 6자리 hex")
    args = parser.parse_args(argv)
    try:
        if args.input.resolve() == args.output.resolve() or args.output.exists():
            raise ValueError("원본 또는 기존 출력 경로를 덮어쓸 수 없음")
        key = args.background.removeprefix("#")
        if len(key) != 6:
            raise ValueError("바탕색은 RGB 6자리 hex")
        background = tuple(bytes.fromhex(key))
        width, height, channels, rows = png.decode(args.input.read_bytes())
        rgba = remove_matte(width, height, channels, rows, background)
        output = fit_sprite(width, rgba, args.id, args.head_top)
        problems = vn.check_sprite(512, 1280, 4, output)
        if problems:
            raise ValueError("; ".join(problems))
        encoded = png.encode_rgba(512, 1280, output)
        limit = sources.load_spec().max_bytes_for("sprite")
        if len(encoded) > limit:
            encoded = png.encode_rgba(512, 1280, output, optimize=True)
        if len(encoded) > limit:
            raise ValueError(f"무손실 재압축 후에도 {len(encoded)} > {limit}바이트: PM 보고")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as handle:
            handle.write(encoded)
        print(json.dumps({"path": str(args.output), "size": [512, 1280], "bytes": len(encoded),
                          "visible_box": vn.visible_box(512, output)}))
        return 0
    except (OSError, ValueError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
