"""임시 장면 배경 그리기(도메인). 배치 확인용 코드 그림(최종 화풍 아님). 내부적으로 320×180
격자에 칠해 ×4 확대 → 1280×720 RGB.

- `meeting`: 낮 마을 마당의 일반 구도(하늘·먼 산·양옆 초가·낮은 담·흙 마당).
- `night`: 먹남 하늘·어두운 지붕선·먹 땅. 창 불빛·달처럼 한 곳을 강조하는 요소는 넣지 않는다.
- 가운데 sprite 영역(격자 x 96~224)과 아래 1/4(격자 y 135~)은 단순하게 둔다(대화창·인물이 덮는다).
- 인물·글자·특정 장소 표시 없음. 색은 스타일 가이드 기준 색(`portrait_art.C`)만. 같은 입력이면 같은 픽셀.
"""
from __future__ import annotations

from tools.assets.portrait_art import C

RGB = tuple[int, int, int]
GW, GH = 320, 180                          # 원본 격자
UNIT = 4
SPRITE_X = (96, 224)                       # 가운데 sprite가 서는 격자 가로 구간(1280 무대의 384~896px)
LOW_Y = 135                                # 이 행부터 아래 1/4(대화창이 덮음)
IDS = ("meeting", "night")


class _Grid:
    def __init__(self, color: RGB) -> None:
        self.px = [[color] * GW for _ in range(GH)]

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
        for y in range(max(0, y0), min(GH, y1)):
            for x in range(max(0, x0), min(GW, x1)):
                self.px[y][x] = color

    def poly(self, pts: list[tuple[float, float]], color: RGB, outline: RGB | None = None) -> None:
        """다각형(격자 좌표)을 픽셀 중심 기준으로 칠하고, outline이 있으면 경계 칸을 그 색으로."""
        ys = [p[1] for p in pts]
        mask: set[tuple[int, int]] = set()
        n = len(pts)
        for y in range(max(0, int(min(ys))), min(GH, int(max(ys)) + 1)):
            yc = y + 0.5
            xs = []
            for i in range(n):
                (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                for x in range(max(0, round(a)), min(GW, round(b))):
                    mask.add((x, y))
        for x, y in mask:
            self.px[y][x] = color
        if outline is None:
            return
        for x, y in mask:
            if any(0 <= x + dx < GW and 0 <= y + dy < GH and (x + dx, y + dy) not in mask
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                self.px[y][x] = outline


def _house(g: _Grid, x0: int, x1: int, ground: int, wall: RGB, roof: RGB, roof_s: RGB, ink: RGB) -> None:
    """초가 한 채(정면). 벽·기둥·문, 둥근 짚 지붕."""
    w = x1 - x0
    top = ground - 34
    g.poly([(x0 + 3, ground), (x0 + 3, top + 12), (x1 - 3, top + 12), (x1 - 3, ground)], wall, ink)
    for px in (x0 + 3, x0 + w // 3, x1 - w // 3, x1 - 4):
        g.rect(px, top + 12, px + 1, ground, roof_s)                       # 기둥
    g.poly([(x0 + w // 2 - 5, ground), (x0 + w // 2 - 5, top + 18), (x0 + w // 2 + 5, top + 18),
            (x0 + w // 2 + 5, ground)], roof_s, ink)                       # 문
    g.poly([(x0 - 4, top + 14), (x0 + 4, top + 2), (x0 + w // 2, top - 2), (x1 - 4, top + 2), (x1 + 4, top + 14)],
           roof, ink)
    g.rect(x0 + 2, top + 12, x1 - 2, top + 13, roof_s)                     # 처마 그림자


def _meeting() -> _Grid:
    sky, sky_s, cloud = C["청"], C["청 그림자"], C["백"]
    g = _Grid(sky)
    g.rect(0, 0, GW, 18, sky_s)
    for cx, cy, w in ((30, 26, 34), (262, 20, 40), (300, 40, 22)):        # 구름(가운데를 피함)
        g.rect(cx - w // 2, cy, cx + w // 2, cy + 4, cloud)
        g.rect(cx - w // 2 + 5, cy - 3, cx + w // 2 - 7, cy, cloud)
    hills = [(0, 80), (0, 66), (40, 58), (90, 64), (140, 56), (190, 62), (240, 54), (290, 62), (320, 58), (320, 80)]
    g.poly([(float(x), float(y)) for x, y in hills], C["녹"], C["먹"])
    g.rect(0, 80, GW, 112, C["백 그림자"])                                  # 먼 마당 끝
    g.rect(0, 96, GW, 112, C["백"])                                        # 낮은 흙담(정면, 단색)
    g.rect(0, 96, GW, 97, C["먹"])
    g.rect(0, 111, GW, 112, C["먹"])
    _house(g, 8, 84, 112, C["한지"], C["황"], C["황 그림자"], C["먹"])
    _house(g, 236, 312, 112, C["한지"], C["황"], C["황 그림자"], C["먹"])
    g.rect(0, 112, GW, GH, C["백 그림자"])                                  # 흙 마당
    g.rect(0, 112, GW, 113, C["황 그림자"])
    return g


def _meeting_ground_detail(g: _Grid) -> None:
    """마당 위쪽(가운데·아래 1/4 밖)의 잔돌 몇 개."""
    for x, y in ((20, 120), (52, 126), (70, 118), (250, 122), (284, 128), (304, 118)):
        g.rect(x, y, x + 3, y + 1, C["한지"])


def _night() -> _Grid:
    g = _Grid(C["깊은 밤"])
    g.rect(0, 50, GW, 100, C["먹남"])
    for x, y in ((14, 10), (41, 24), (66, 8), (80, 38), (238, 14), (262, 30), (290, 9), (309, 40), (20, 52),
                 (300, 60)):
        g.rect(x, y, x + 1, y + 1, C["한지"])                               # 별(가운데 sprite 구간 밖)
    ink, wall = C["먹"], C["깊은 밤"]
    g.rect(0, 104, GW, 118, ink)                                           # 가운데는 낮은 담 지붕선만
    for x0, x1, top in ((-10, 46, 80), (40, 98, 88), (222, 280, 88), (274, 330, 80)):   # 양옆 초가 지붕선
        base = top + 12
        g.rect(x0 + 4, base, x1 - 4, 118, wall)
        g.poly([(x0 - 2.0, base + 1.0), (x0 + 6.0, top + 3.0), ((x0 + x1) / 2, top - 1.0), (x1 - 6.0, top + 3.0),
                (x1 + 2.0, base + 1.0)], ink)
        g.rect(x0 + 4, base + 1, x1 - 4, base + 2, ink)                    # 처마 그림자
    g.rect(0, 118, GW, GH, ink)                                            # 땅
    return g


def draw(cid: str) -> list[list[RGB]]:
    """배경 격자(320×180). 모르는 id면 ValueError."""
    if cid == "meeting":
        g = _meeting()
        _meeting_ground_detail(g)
    elif cid == "night":
        g = _night()
    else:
        raise ValueError(f"알 수 없는 배경 id: {cid!r} (가능: {', '.join(IDS)})")
    return g.px


def render(cid: str, width: int, height: int) -> list[bytearray]:
    """1280×720(격자 320×180 × 정수 배율) RGB 행 목록. 크기가 맞지 않으면 ValueError."""
    if width <= 0 or width % GW or height * GW != width * GH:
        raise ValueError(f"배경 크기는 {GW}×{GH}의 정수배여야 함: {width}x{height}")
    k = width // GW
    rows: list[bytearray] = []
    for line in draw(cid):
        row = bytearray()
        for c in line:
            row += bytes(c) * k
        rows.extend(bytearray(row) for _ in range(k))
    return rows
