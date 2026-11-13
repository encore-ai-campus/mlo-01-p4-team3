"""임시 에셋 이름표 글자 찍기(도메인). 저장소에 커밋한 비트맵(`glyph_data.py`)만 쓰므로 어느 노트북에서나 같은 픽셀이다.

- `fits(text)`: 모든 글자의 비트맵이 있는가(공백은 늘 있음).
- `width(text, scale)`, `HEIGHT`: 크기 계산. 공백 폭은 `SPACE`.
- `draw(rows, channels, x, y, text, color, scale)`: RGB(3)·RGBA(4) 행 목록에 글자를 덮어 그린다. RGBA면 알파 255.
  비트맵이 없는 글자는 건너뛴다(호출 쪽이 `fits`로 대체 문구를 고른다).
"""
from __future__ import annotations

from typing import MutableSequence, Sequence

from tools.assets.glyph_data import GLYPHS, HEIGHT

SPACE = HEIGHT // 3


def fits(text: str) -> bool:
    return all(ch.isspace() or ch in GLYPHS for ch in text)


def width(text: str, scale: int = 1) -> int:
    return sum(SPACE if ch.isspace() else GLYPHS[ch][0] if ch in GLYPHS else 0 for ch in text) * scale


def draw(rows: MutableSequence[bytearray], channels: int, x: int, y: int, text: str,
         color: Sequence[int], scale: int = 1) -> None:
    px = bytes(color[:3]) + (b"\xff" if channels == 4 else b"")
    block = px * scale
    height = len(rows)
    row_w = len(rows[0]) // channels if rows else 0
    for ch in text:
        if ch.isspace():
            x += SPACE * scale
            continue
        if ch not in GLYPHS:
            continue
        w, bitmap = GLYPHS[ch]
        for gy, bits in enumerate(bitmap):
            if not bits:
                continue
            for gx in range(w):
                if not bits >> (w - 1 - gx) & 1:
                    continue
                x0 = x + gx * scale
                if x0 < 0 or x0 + scale > row_w:
                    continue
                for dy in range(scale):
                    yy = y + gy * scale + dy
                    if 0 <= yy < height:
                        rows[yy][x0 * channels:(x0 + scale) * channels] = block
        x += w * scale
