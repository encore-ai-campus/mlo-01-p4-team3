"""임시 장면 배경 그리기(도메인). 위→아래 2색 그라데이션 + 왼쪽 위 이름표. 크기·배치 확인용이며 최종 그림이 아니다.

- `meeting`: 밝은 회갈색, 이름표 "회의 배경(임시)". `night`: 어두운 남색, 이름표 "밤 배경(임시)".
- 이름표는 왼쪽 위(`LABEL_XY`)에 2배로 찍는다. 가운데는 평소 표시의 sprite(가로 435~845px)가 덮으므로 피한다.
- 인물·장소 표시·강조 요소 없음. 1280×720 RGB(불투명). 같은 입력이면 같은 픽셀.
"""
from __future__ import annotations

from tools.assets import glyphs

RGB = tuple[int, int, int]
W, H = 1280, 720
IDS = ("meeting", "night")
LABELS = {"meeting": "회의 배경(임시)", "night": "밤 배경(임시)"}
LABEL_XY = (40, 32)
LABEL_SCALE = 2
COLORS: dict[str, tuple[RGB, RGB, RGB]] = {          # (위, 아래, 글자)
    "meeting": ((0xD2, 0xC8, 0xB6), (0xA8, 0x9C, 0x88), (0x4A, 0x42, 0x38)),
    "night": ((0x2A, 0x33, 0x4F), (0x12, 0x16, 0x22), (0xC8, 0xC2, 0xB4)),
}


def render(cid: str, width: int, height: int) -> list[bytearray]:
    """1280×720 RGB 행 목록. 모르는 id나 다른 크기는 ValueError."""
    if cid not in COLORS:
        raise ValueError(f"알 수 없는 장면 배경: {cid!r} (허용 {IDS})")
    if (width, height) != (W, H):
        raise ValueError(f"장면 배경 크기는 {W}x{H}여야 함: {width}x{height}")
    top, bottom, ink = COLORS[cid]
    rows = []
    for y in range(H):
        t = y / (H - 1)
        c = bytes(round(a + (b - a) * t) for a, b in zip(top, bottom))
        rows.append(bytearray(c * W))
    glyphs.draw(rows, 3, *LABEL_XY, LABELS[cid], ink, LABEL_SCALE)
    return rows
