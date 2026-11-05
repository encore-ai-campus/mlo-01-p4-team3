"""임시 마스코트 그리기(도메인). 단순한 회색 동물 실루엣 + "임시"/이름. 크기·배치 확인용이며 최종 그림이 아니다.

- `kkachi`(까치, 아침 소식): 둥근 몸·머리·부리·긴 꼬리. `jokjebi`(족제비, 무당의 짝·안내): 긴 몸·머리·귀·꼬리.
- 512×512 RGBA, 바탕 투명. 이름표 2줄은 몸 위 가운데. 연출·안내용이라 게임 정보를 담지 않는다.
- 같은 id면 같은 픽셀.
"""
from __future__ import annotations

from tools.assets import glyphs

RGB = tuple[int, int, int]
SIZE = 512
LABELS = {"kkachi": "까치", "jokjebi": "족제비"}
PREFIX = "임시"
FILL: RGB = (0x9A, 0x9A, 0x9A)
TEXT: RGB = (0xF4, 0xF4, 0xF4)

Shape = tuple  # ("e", cx, cy, rx, ry) 타원 | ("t", (x, y), (x, y), (x, y)) 삼각형

SHAPES: dict[str, tuple[Shape, ...]] = {
    "kkachi": (
        ("e", 230, 300, 150, 105),                     # 몸
        ("e", 350, 190, 70, 64),                       # 머리
        ("t", (410, 170), (480, 195), (410, 215)),     # 부리
        ("t", (110, 280), (20, 430), (150, 350)),      # 꼬리
    ),
    "jokjebi": (
        ("e", 250, 320, 190, 80),                      # 긴 몸
        ("e", 400, 220, 70, 58),                       # 머리
        ("t", (365, 175), (380, 120), (405, 170)),     # 귀
        ("t", (415, 170), (440, 118), (455, 175)),     # 귀
        ("t", (80, 300), (10, 210), (110, 340)),       # 꼬리
    ),
}


def _inside(shape: Shape, x: float, y: float) -> bool:
    if shape[0] == "e":
        _, cx, cy, rx, ry = shape
        return ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 <= 1
    _, a, b, c = shape
    def side(p, q):
        return (q[0] - p[0]) * (y - p[1]) - (q[1] - p[1]) * (x - p[0])
    s1, s2, s3 = side(a, b), side(b, c), side(c, a)
    return (s1 >= 0 and s2 >= 0 and s3 >= 0) or (s1 <= 0 and s2 <= 0 and s3 <= 0)


def render(cid: str, width: int, height: int) -> list[bytearray]:
    """512×512 RGBA 행 목록. 모르는 id나 다른 크기는 ValueError."""
    if cid not in SHAPES:
        raise ValueError(f"알 수 없는 마스코트: {cid!r} (허용 {tuple(SHAPES)})")
    if (width, height) != (SIZE, SIZE):
        raise ValueError(f"마스코트 크기는 {SIZE}x{SIZE}여야 함: {width}x{height}")
    px = bytes((*FILL, 255))
    rows = []
    for y in range(SIZE):
        line = bytearray(SIZE * 4)
        for x in range(SIZE):
            if any(_inside(s, x + 0.5, y + 0.5) for s in SHAPES[cid]):
                line[x * 4:x * 4 + 4] = px
        rows.append(line)
    for i, text in enumerate((PREFIX, LABELS[cid])):
        glyphs.draw(rows, 4, 240 - glyphs.width(text) // 2, 250 + i * (glyphs.HEIGHT + 6), text, TEXT)
    return rows
