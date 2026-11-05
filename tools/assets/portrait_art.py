"""임시 sprite·초상 그리기(도메인). 실루엣·배치 확인용 코드 그림이며 최종 화풍(반실사 일러스트)이 아니다.

- sprite: 사람마다 전신 1장, 512×1280 RGBA(바탕 투명). 128×320 격자에 그려 ×4 최근접 확대한다(임시 그림의 그리기
  방식일 뿐 규격이 아니다). 기준선: 눈높이 y=200(`EYE_Y`), 허리선 y=640(`WAIST_Y`), 발끝 y=1248(`FOOT_Y`).
- 머리·얼굴·모자는 기존 흉상 그림(512 좌표)을 0.8배·위로 옮겨 그리고(`HEAD_SCALE`, `HEAD_DY`), 몸통·하의·신발은
  최종 픽셀 좌표로 그린다.
- 초상: sprite 위 512×512를 잘라 먹남 위에 알파 합성한다(`crop_portrait`, 실제 sprite에도 같은 함수를 쓴다).
- 색은 스타일 가이드의 기준 색(`PALETTE`, 임시 그림만 이 색으로 제한), 1px 먹 외곽선. 그리는 순서는 뒤 → 앞(뒷머리 → 하의 → 저고리·팔 → 목·깃 → 귀 → 얼굴
  → 앞머리 → 모자).
- 인물은 `CHARACTERS` 데이터(머리 모양·모자·옷·하의·소품)로 정한다. 얼굴 틀(머리 크기·눈높이·눈 모양)은 모두 같다.
  주인공(무당 2종)만 눈 하이라이트 2점, 주민은 전원 1점(스타일 가이드 판정표). 해시나 게임 규칙으로 모양을 고르지 않는다.
- 모르는 id는 `DEFAULT_RESIDENT`(평범한 주민)로 그린다. 같은 입력이면 같은 픽셀이다.
"""
from __future__ import annotations

import math
import re

RGB = tuple[int, int, int]
GRID = 128                     # 가로 격자(512px ÷ 4)
SPRITE_ROWS = 320              # sprite 세로 격자(1280px ÷ 4)
SRC = 512                      # 도형 좌표의 기준 크기(가로 px)
UNIT = SRC / GRID              # px → 격자 1칸
EYE_Y = 200                    # 눈높이(눈동자 중심 y, px). spec.md 2절 기준선과 같다
WAIST_Y = 640                  # 허리선 y(px). 평소 표시는 0~640행을 쓴다
FOOT_Y = 1248                  # 발끝 y(px). 아래 여백 32px
HEAD_SCALE = 0.8               # 흉상 그림(512 좌표) → sprite 머리 배율
HEAD_DY = -4.0                 # 흉상 눈 중심 y 255 × 0.8 − 4 ≈ 200

# 스타일 가이드 3절 표와 같은 순서·값(tests가 대조한다)
PALETTE: dict[str, str] = {
    "먹남": "#2E344A", "깊은 밤": "#1F2433", "먹": "#26231F", "머리 하이라이트": "#4A433C",
    "한지": "#E8DFC8", "백": "#F2EEE4", "백 그림자": "#D6CFC0", "적": "#B04A3A", "적 그림자": "#8E3A2D",
    "청": "#3A6EB0", "청 그림자": "#2C5690", "황": "#C9A23A", "황 그림자": "#9C7A26", "녹": "#4E7A5C",
    "피부": "#E5C3A0", "피부 그림자": "#C99A74", "입술": "#B8705F",
}


def _rgb(code: str) -> RGB:
    return (int(code[1:3], 16), int(code[3:5], 16), int(code[5:7], 16))


C: dict[str, RGB] = {k: _rgb(v) for k, v in PALETTE.items()}
BG, NIGHT, INK, HAIR_HI = C["먹남"], C["깊은 밤"], C["먹"], C["머리 하이라이트"]
HANJI, WHITE, WHITE_S = C["한지"], C["백"], C["백 그림자"]
RED, RED_S, BLUE, BLUE_S = C["적"], C["적 그림자"], C["청"], C["청 그림자"]
GOLD, GOLD_S, GREEN = C["황"], C["황 그림자"], C["녹"]
SKIN, SKIN_S, LIP = C["피부"], C["피부 그림자"], C["입술"]



def _head_grid(gx: float, gy: float) -> tuple[float, float]:
    """흉상 격자 좌표 → sprite 격자 좌표(머리 변환)."""
    return GRID / 2 + (gx - GRID / 2) * HEAD_SCALE, gy * HEAD_SCALE + HEAD_DY / UNIT


def _head_box(x0: float, y0: float, x1: float, y1: float) -> tuple[int, int, int, int]:
    (a, b), (c, d) = _head_grid(x0, y0), _head_grid(x1, y1)
    return math.floor(a), math.floor(b), max(math.ceil(c), math.floor(a) + 1), max(math.ceil(d), math.floor(b) + 1)


# 눈 영역(sprite 격자 좌표, [x0, x1) × [y0, y1)). 흉상 기준 (44, 58, 62, 70)·(66, 58, 84, 70)을 머리 변환한 값.
# 테스트가 하이라이트 수를 센다.
EYE_BOXES: tuple[tuple[int, int, int, int], ...] = (_head_box(44, 58, 62, 70), _head_box(66, 58, 84, 70))


# ---------------------------------------------------------------- 래스터화

class Grid:
    """가로 128 색 격자. 세로는 `rows`(초상 128, sprite 160), 빈 칸은 `bg`(None = 투명)."""

    def __init__(self, rows: int = GRID, bg: RGB | None = BG) -> None:
        self.w, self.h = GRID, rows
        self.px: list[list[RGB | None]] = [[bg] * GRID for _ in range(rows)]
        self.head = False                  # True면 좌표를 머리 변환(0.8배, 위로)해서 칠한다

    def _g(self, x: float, y: float) -> tuple[float, float]:
        """512 좌표(px) → 격자 좌표. 머리 모드면 머리 변환을 거친다."""
        if self.head:
            x, y = SRC / 2 + (x - SRC / 2) * HEAD_SCALE, y * HEAD_SCALE + HEAD_DY
        return x / UNIT, y / UNIT

    def _paint(self, mask: set[tuple[int, int]], color: RGB, outline: bool) -> None:
        for x, y in mask:
            self.px[y][x] = color
        if not outline:
            return
        for x, y in mask:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.w and 0 <= ny < self.h and (nx, ny) not in mask:
                    self.px[y][x] = INK
                    break

    def fill(self, pts: list[tuple[float, float]], color: RGB, outline: bool = True) -> None:
        """다각형(512 좌표)을 칠한다. 픽셀 중심 기준, 짝홀 규칙."""
        self._paint(self._mask(pts), color, outline)

    def edge(self, pts: list[tuple[float, float]]) -> None:
        """다각형(512 좌표)의 경계 칸만 먹으로 다시 칠한다(그림자 면이 덮은 바깥 윤곽 복구)."""
        mask = self._mask(pts)
        for x, y in mask:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.w and 0 <= ny < self.h and (nx, ny) not in mask:
                    self.px[y][x] = INK
                    break

    def _mask(self, pts: list[tuple[float, float]]) -> set[tuple[int, int]]:
        g = [self._g(x, y) for x, y in pts]
        ys = [p[1] for p in g]
        mask: set[tuple[int, int]] = set()
        n = len(g)
        for y in range(max(0, int(min(ys))), min(self.h, int(max(ys)) + 1)):
            yc = y + 0.5
            xs = []
            for i in range(n):
                (x0, y0), (x1, y1) = g[i], g[(i + 1) % n]
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                for x in range(max(0, math.ceil(a - 0.5)), min(self.w, math.ceil(b - 0.5))):
                    mask.add((x, y))
        return mask

    def line(self, pts: list[tuple[float, float]], width: float, color: RGB) -> None:
        """굵은 선(512 좌표, 굵기도 512 기준). 선을 따라 원을 찍는다."""
        r = max(width * (HEAD_SCALE if self.head else 1) / UNIT / 2, 0.5)
        mask: set[tuple[int, int]] = set()
        g = [self._g(x, y) for x, y in pts]
        for (x0, y0), (x1, y1) in zip(g, g[1:] or g):
            steps = max(1, int(math.hypot(x1 - x0, y1 - y0) * 4))
            for i in range(steps + 1):
                cx, cy = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
                for y in range(int(cy - r) - 1, int(cy + r) + 2):
                    for x in range(int(cx - r) - 1, int(cx + r) + 2):
                        if 0 <= x < self.w and 0 <= y < self.h and (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                            mask.add((x, y))
        self._paint(mask, color, False)

    def block(self, x0: float, y0: float, x1: float, y1: float, color: RGB) -> None:
        """격자 좌표 사각형 [x0, x1) × [y0, y1)을 칠한다(하이라이트 점처럼 픽셀을 정확히 놓을 때).

        머리 모드면 흉상 격자 좌표로 보고 머리 변환한다(최소 1칸).
        """
        if self.head:
            x0, y0, x1, y1 = _head_box(x0, y0, x1, y1)
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                if 0 <= x < self.w and 0 <= y < self.h:
                    self.px[y][x] = color


_TOKEN = re.compile(r"[MLCQZ]|-?\d+(?:\.\d+)?")


def path(d: str, mirror: bool = False) -> list[tuple[float, float]]:
    """SVG 경로 일부(M, L, C, Q, Z, 절대 좌표)를 점열로 바꾼다. 곡선은 12등분. mirror면 좌우 반전(x → 512 - x)."""
    toks = _TOKEN.findall(d)
    pts: list[tuple[float, float]] = []
    i, cmd = 0, "M"

    def num() -> float:
        nonlocal i
        v = float(toks[i]); i += 1
        return v

    while i < len(toks):
        if toks[i] in "MLCQZ":
            cmd = toks[i]; i += 1
            if cmd == "Z":
                continue
        if cmd in "ML":
            pts.append((num(), num()))
        elif cmd == "C":
            p0 = pts[-1]
            p1, p2, p3 = (num(), num()), (num(), num()), (num(), num())
            for k in range(1, 13):
                t = k / 12; u = 1 - t
                pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
        elif cmd == "Q":
            p0 = pts[-1]
            p1, p2 = (num(), num()), (num(), num())
            for k in range(1, 13):
                t = k / 12; u = 1 - t
                pts.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                            u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    if mirror:
        pts = [(SRC - x, y) for x, y in pts]
    return pts


def ellipse(cx: float, cy: float, rx: float, ry: float) -> list[tuple[float, float]]:
    """타원 다각형(512 좌표, 48각형)."""
    return [(cx + rx * math.cos(2 * math.pi * k / 48), cy + ry * math.sin(2 * math.pi * k / 48)) for k in range(48)]


def both(g: Grid, d: str, color: RGB, outline: bool = True) -> None:
    """왼쪽 도형과 그 좌우 반전을 함께 칠한다."""
    g.fill(path(d), color, outline)
    g.fill(path(d, True), color, outline)


# ---------------------------------------------------------------- 인물 데이터

# hair: 머리 모양, hat: 모자, cloth: 저고리(기본색, 그림자색, 깃), outer: 겉옷, lower: 하의(종류, 색, 그림자색), extra: 소품
CHARACTERS: dict[str, dict] = {
    "mudang_f": {"hero": True, "sex": "f", "hair": "bangs_long", "hat": None,
                 "cloth": (WHITE, WHITE_S, HANJI), "outer": ("vest", RED, RED_S), "lower": ("chima", BLUE, BLUE_S), "extra": ["binyeo"]},
    "mudang_m": {"hero": True, "sex": "m", "hair": "bangs_short", "hat": None,
                 "cloth": (WHITE, WHITE_S, HANJI), "outer": ("vest", RED, RED_S), "lower": ("baji", WHITE, WHITE_S), "extra": []},
    "hunjang": {"sex": "m", "hair": "pulled", "hat": "gat",
                "cloth": (WHITE, WHITE_S, HANJI), "outer": None, "lower": ("baji", WHITE, WHITE_S), "extra": ["beard"]},
    "jumo": {"sex": "f", "hair": "part_bun", "hat": None,
             "cloth": (BLUE, BLUE_S, HANJI), "outer": None, "lower": ("chima", GREEN, GREEN), "extra": ["binyeo", "goreum_red", "apron"]},
    "blacksmith": {"sex": "m", "hair": "pulled", "hat": "dugeon",
                   "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("apron", NIGHT, NIGHT), "lower": ("baji", HANJI, WHITE_S), "extra": []},
    "meosum": {"sex": "m", "hair": "pulled", "hat": "headband",
               "cloth": (HANJI, WHITE_S, WHITE_S), "outer": None, "lower": ("baji", HANJI, WHITE_S), "extra": []},
    "yakcho": {"sex": "m", "hair": "pulled", "hat": "towel",
               "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("vest", GREEN, GREEN), "lower": ("baji", HANJI, WHITE_S), "extra": []},
    "gwabu": {"sex": "f", "hair": "part_bun", "hat": None,
              "cloth": (WHITE, WHITE_S, WHITE_S), "outer": None, "lower": ("chima", WHITE, WHITE_S), "extra": ["binyeo_wood"]},
    "ferryman": {"sex": "m", "hair": "pulled", "hat": "paeraengi",
                 "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("vest", BLUE, BLUE_S), "lower": ("baji", WHITE_S, WHITE_S), "extra": []},
    "gisaeng": {"sex": "f", "hair": "gache", "hat": None,
                "cloth": (GOLD, GOLD_S, HANJI), "outer": None, "lower": ("chima", RED, RED_S), "extra": ["goreum_red"]},
    "hunter": {"sex": "m", "hair": "pulled", "hat": "fur",
               "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("vest", GOLD_S, GOLD_S), "lower": ("baji", HANJI, WHITE_S), "extra": []},
    "monk": {"sex": "m", "hair": "bald", "hat": None,
             "cloth": (NIGHT, NIGHT, HANJI), "outer": None, "lower": ("robe", NIGHT, NIGHT), "extra": ["kasa"]},
    "boy": {"sex": "m", "hair": "braid", "hat": None,
            "cloth": (HANJI, WHITE_S, WHITE_S), "outer": None, "lower": ("baji", HANJI, WHITE_S), "extra": []},
}
DEFAULT_RESIDENT: dict = {"sex": "m", "hair": "pulled", "hat": None,
                          "cloth": (HANJI, WHITE_S, WHITE_S), "outer": None,
                          "lower": ("baji", HANJI, WHITE_S), "extra": []}


def character(cid: str) -> dict:
    """id의 인물 데이터(모르면 평범한 주민)."""
    return CHARACTERS.get(cid, DEFAULT_RESIDENT)


# ---------------------------------------------------------------- 부위

FACE = ("M164 230 C164 152 204 110 256 110 C308 110 348 152 348 230 C348 262 342 288 326 312 "
        "C310 336 286 356 256 362 C226 356 202 336 186 312 C170 288 164 262 164 230 Z")
FACE_SHADE = "M322 160 C342 190 348 220 344 252 C340 290 320 326 290 350 C314 318 328 284 330 248 C332 212 330 184 322 160 Z"


def _back_hair(g: Grid, ch: dict) -> None:
    hair = ch["hair"]
    if hair in ("bangs_long", "part_bun", "gache"):
        g.fill(path("M136 250 C128 140 196 84 256 84 C316 84 384 140 376 250 C378 300 368 336 352 360 L160 360 "
                    "C144 336 134 300 136 250 Z"), INK)
    elif hair in ("bangs_short", "braid"):
        g.fill(path("M146 236 C138 140 196 88 256 88 C316 88 374 140 366 236 C366 256 360 272 352 282 L160 282 "
                    "C152 272 146 256 146 236 Z"), INK)
    if "binyeo" in ch["extra"] or "binyeo_wood" in ch["extra"]:
        col, sh = (GOLD, GOLD_S) if "binyeo" in ch["extra"] else (GOLD_S, GOLD_S)
        g.fill(path("M110 298 L402 298 L402 312 L110 312 Z"), col)
        g.block(29, 76, 100, 77, sh)


def _lower(g: Grid, ch: dict) -> None:
    """하의·신발(최종 px 좌표). 허리선 600~640에서 시작해 발끝 FOOT_Y까지."""
    kind, base, shade = ch["lower"]
    if kind == "chima":
        g.fill(path("M150 600 L362 600 C380 760 400 1000 412 1232 L100 1232 C112 1000 132 760 150 600 Z"), base)
        g.fill(path("M300 600 L362 600 C380 760 400 1000 412 1232 L336 1232 C330 1000 318 760 300 600 Z"),
               shade, False)
        g.edge(path("M150 600 L362 600 C380 760 400 1000 412 1232 L100 1232 C112 1000 132 760 150 600 Z"))
        for cx in (214, 298):
            g.fill(ellipse(cx, FOOT_Y - 12, 24, 12), WHITE)          # 버선코
    elif kind == "robe":
        g.fill(path("M136 600 L376 600 L394 1228 L118 1228 Z"), base)
        for cx in (210, 302):
            g.fill(ellipse(cx, FOOT_Y - 12, 30, 12), GOLD_S)
    else:                                                            # 바지
        legs = path("M146 600 L366 600 L378 900 L364 1192 L270 1192 L262 836 L250 836 L242 1192 L148 1192 "
                    "L134 900 Z")
        g.fill(legs, base)
        g.fill(path("M300 600 L366 600 L378 900 L364 1192 L330 1192 L338 900 Z"), shade, False)
        g.edge(legs)
        g.fill(path("M150 1168 L242 1168 L242 1192 L148 1192 Z"), shade)   # 대님
        g.fill(path("M270 1168 L362 1168 L364 1192 L270 1192 Z"), shade)
        for cx in (196, 316):
            g.fill(ellipse(cx, FOOT_Y - 20, 48, 20), GOLD_S)          # 짚신


def _torso(g: Grid, ch: dict) -> None:
    """저고리·팔·겉옷(최종 px 좌표). 어깨선 y≈316, 허리선 WAIST_Y."""
    base, shade, _ = ch["cloth"]
    body = path("M118 406 C124 346 179 319 256 316 C333 319 387 346 393 406 L400 652 L112 652 Z")
    g.fill(body, base)
    g.fill(path("M300 320 C360 330 390 360 393 406 L400 652 L352 652 L352 420 C340 370 320 340 300 330 Z"),
           shade, False)
    g.edge(body)                                                    # 그림자가 덮은 오른쪽 윤곽
    g.line(path("M164 420 L160 652"), 4, shade)                     # 팔과 몸통 사이 주름
    g.line(path("M348 420 L352 652"), 4, shade)
    outer = ch["outer"]
    if outer and outer[0] == "vest":
        g.fill(path("M146 380 C160 350 190 334 222 330 L242 410 L236 640 L166 640 L166 420 Z"), outer[1])
        g.fill(path("M366 380 C352 350 322 334 290 330 L270 410 L276 640 L346 640 L346 420 Z"), outer[2])
    if outer and outer[0] == "apron":
        g.fill(path("M200 352 L312 352 L320 420 L332 780 L180 780 L192 420 Z"), outer[1])
        g.line(path("M206 356 L186 326"), 8, outer[1])
        g.line(path("M306 356 L326 326"), 8, outer[1])
    if "apron" in ch["extra"]:
        g.fill(path("M204 400 L308 400 L318 640 L324 900 L188 900 L194 640 Z"), WHITE)
    if "kasa" in ch["extra"]:
        g.fill(path("M150 360 L190 340 L396 560 L396 640 L320 640 Z"), RED_S)
    for cx in (136, 376):                                            # 손(소매 끝)
        g.fill(ellipse(cx, 676, 20, 26), SKIN)


def _neck_collar(g: Grid, ch: dict) -> None:
    """목·깃·고름(흉상 512 좌표, 머리 변환으로 그린다)."""
    collar = ch["cloth"][2]
    g.fill(path("M234 330 L234 412 L278 412 L278 330 Z"), SKIN_S)
    g.fill(path("M206 404 L256 474 L306 404 L290 400 L256 450 L222 400 Z"), collar)
    if "goreum_red" in ch["extra"]:
        g.fill(path("M262 462 L300 470 L292 500 L266 478 Z"), RED)
        g.fill(path("M260 466 L282 548 L264 548 L252 470 Z"), RED)


def _ears(g: Grid, ch: dict) -> None:
    if ch["hair"] in ("pulled", "bald"):
        g.fill(ellipse(166, 258, 16, 24), SKIN)
        g.fill(ellipse(346, 258, 16, 24), SKIN_S)


def _eye(g: Grid, hero: bool, mirror: bool, sex: str = "f") -> None:
    """왼눈을 그린다(mirror면 오른눈). 흰자는 한지, 하이라이트는 백."""
    def P(d: str) -> list[tuple[float, float]]:
        return path(d, mirror)
    g.fill(ellipse(SRC - 212 if mirror else 212, 254, 24, 19), HANJI, False)
    px = SRC - 214 if mirror else 214
    g.fill(ellipse(px, 255, 14, 19), INK, False)
    g.fill(ellipse(px, 264, 9, 7), HAIR_HI, False)
    if sex == "f":
        g.line(P("M186 246 Q212 222 240 242"), 9, INK)                # 위 속눈썹(굵은 호)
        g.line(P("M188 246 L178 240"), 6, INK)                        # 바깥 눈꼬리
    else:
        g.line(P("M190 242 Q212 232 236 240"), 4, INK)                # 남: 속눈썹 없이 얇고 평평한 윗눈꺼풀
    g.line(P("M198 273 Q212 276 228 271"), 4, SKIN_S)                 # 아래 눈꺼풀
    # 하이라이트(격자 좌표): 큰 점 2×2(위·바깥), 작은 점 1×1(아래·안) — 주인공만 작은 점
    big = (52, 61, 54, 63)
    small = (55, 65, 56, 66)
    for x0, y0, x1, y1 in ([big, small] if hero else [big]):
        if mirror:
            x0, x1 = GRID - x1, GRID - x0
        g.block(x0, y0, x1, y1, WHITE)


def _face(g: Grid, ch: dict) -> None:
    hero = ch.get("hero", False)
    g.fill(path(FACE), SKIN)
    g.fill(path(FACE_SHADE), SKIN_S, False)
    _eye(g, hero, False, ch["sex"])
    _eye(g, hero, True, ch["sex"])
    brow = 5 if ch["sex"] == "f" else 8
    g.line(path("M194 216 Q214 208 234 214"), brow, INK)
    g.line(path("M194 216 Q214 208 234 214", True), brow, INK)
    g.fill(path("M260 288 L252 302 L264 302 Z"), SKIN_S, False)        # 코
    if ch["sex"] == "f":
        g.fill(path("M244 330 Q256 337 268 330 Q256 341 244 330 Z"), LIP, False)
        g.line(path("M241 328 Q256 334 271 328"), 5, INK)
    else:
        g.line(path("M244 330 Q256 334 268 330"), 5, INK)
    if "beard" in ch["extra"]:
        g.fill(path("M226 344 C240 362 272 362 286 344 L276 414 L256 430 L236 414 Z"), WHITE_S)
        both(g, "M228 318 C238 308 250 310 254 316 L254 322 C244 318 236 320 228 324 Z", WHITE_S)


def _front_hair(g: Grid, ch: dict) -> None:
    hair = ch["hair"]
    if hair == "bangs_long":
        g.fill(path("M156 236 C150 150 200 92 256 92 C312 92 362 150 356 236 L342 200 L326 206 L312 178 L296 202 "
                    "L278 172 L262 198 L246 172 L230 202 L214 178 L198 206 L182 198 L170 214 Z"), INK)
        both(g, "M156 196 C150 280 160 340 180 364 L196 330 C184 300 178 262 186 206 Z", INK)
    elif hair in ("bangs_short", "braid"):
        g.fill(path("M156 236 C150 150 200 92 256 92 C312 92 362 150 356 236 L342 200 L326 196 L312 172 L296 194 "
                    "L278 166 L262 190 L246 166 L230 194 L214 172 L198 196 L182 200 L170 214 Z"), INK)
        both(g, "M156 196 C152 230 154 252 162 268 L180 258 C178 240 178 226 186 206 Z", INK)
        if hair == "braid":
            for k in range(5):
                g.fill(ellipse(176, 330 + k * 22, 15, 13), INK)
            g.fill(path("M160 442 L192 442 L200 470 L176 460 L152 470 Z"), RED)
    elif hair in ("part_bun", "gache"):
        g.fill(path("M150 250 C140 140 200 88 256 88 C312 88 372 140 362 250 L344 250 C344 196 318 160 264 150 "
                    "L256 140 L248 150 C194 160 168 196 168 250 Z"), INK)
        both(g, "M150 240 C148 290 158 330 176 352 L190 320 C178 294 172 270 170 236 Z", INK)
        if hair == "gache":
            g.fill(ellipse(256, 92, 150, 46), INK)
            g.line(path("M140 92 Q256 60 372 92"), 10, HAIR_HI)
            g.fill(ellipse(256, 130, 14, 14), GOLD)
            g.fill(ellipse(156, 118, 10, 10), RED)
            g.fill(ellipse(356, 118, 10, 10), RED)
    elif hair == "pulled":
        g.fill(path("M156 240 C148 140 200 92 256 92 C312 92 364 140 356 240 L342 230 C340 180 320 158 256 152 "
                    "C192 158 172 180 170 230 Z"), INK)
    # 머리 하이라이트 띠(대머리·모자에 가리는 경우도 일단 그린다. 모자가 위에 덮는다)
    if hair != "bald":
        g.line(path("M190 158 C208 128 232 114 250 110"), 12, HAIR_HI)
        g.line(path("M290 116 C306 122 320 134 330 148"), 8, HAIR_HI)
    else:
        g.line(path("M196 150 C214 126 236 116 252 114"), 10, WHITE_S)


def _hat(g: Grid, ch: dict) -> None:
    hat = ch["hat"]
    if hat == "gat":
        g.fill(path("M206 132 L212 52 C230 40 282 40 300 52 L306 132 Z"), INK)
        g.line(path("M222 120 L226 60"), 6, HAIR_HI)
        g.fill(ellipse(256, 136, 172, 22), INK)
        g.line(path("M110 136 Q256 120 402 136"), 6, HAIR_HI)
    elif hat == "paeraengi":
        g.fill(path("M210 140 L218 74 C236 62 276 62 294 74 L302 140 Z"), GOLD)
        g.fill(path("M268 70 C284 70 294 74 294 74 L302 140 L278 140 Z"), GOLD_S, False)
        g.fill(ellipse(256, 144, 150, 22), GOLD)
        g.fill(path("M214 126 L298 126 L300 140 L212 140 Z"), INK)
    elif hat == "dugeon":
        g.fill(path("M154 210 C146 130 200 84 256 84 C312 84 366 130 358 210 C330 176 300 166 256 166 "
                    "C212 166 182 176 154 210 Z"), BLUE)
        g.fill(path("M300 96 C340 116 362 160 358 210 C346 196 332 184 316 176 Z"), BLUE_S, False)
        g.fill(ellipse(356, 196, 16, 14), BLUE_S)
    elif hat == "headband":
        g.fill(path("M158 170 C200 150 312 150 354 170 L356 192 C312 174 200 174 156 192 Z"), RED)
        g.fill(path("M352 176 L384 200 L374 214 L348 192 Z"), RED_S)
    elif hat == "towel":
        g.fill(path("M156 176 C200 148 312 148 356 176 L358 204 C312 182 200 182 154 204 Z"), HANJI)
        g.fill(path("M350 180 L382 168 L392 190 L356 202 Z"), WHITE_S)
    elif hat == "fur":
        g.fill(path("M150 200 C144 120 200 76 256 76 C312 76 368 120 362 200 C330 172 300 164 256 164 "
                    "C212 164 182 172 150 200 Z"), GOLD_S)
        g.fill(path("M148 190 C176 168 220 160 256 160 C292 160 336 168 364 190 L364 214 C336 194 292 184 256 184 "
                    "C220 184 176 194 148 214 Z"), WHITE_S)
        both(g, "M148 200 L178 204 L182 290 L156 284 Z", GOLD_S)


def draw(cid: str) -> Grid:
    """인물 하나를 sprite 격자(128×320, 바탕 투명)에 전신으로 그린다. 외형은 공개 id(`character`)로만 정한다."""
    ch = character(cid)
    g = Grid(SPRITE_ROWS, None)
    g.head = True
    _back_hair(g, ch)
    g.head = False
    _lower(g, ch)
    _torso(g, ch)
    g.head = True
    _neck_collar(g, ch)
    _ears(g, ch)
    _face(g, ch)
    _front_hair(g, ch)
    _hat(g, ch)
    g.head = False
    return g


def _scale_rows(px: list[list[RGB | None]], k: int) -> list[bytearray]:
    """격자 → RGBA 행(×k 최근접). 투명 칸은 (0, 0, 0, 0), 나머지는 알파 255."""
    rows: list[bytearray] = []
    for line in px:
        row = bytearray()
        for c in line:
            row += (bytes((0, 0, 0, 0)) if c is None else bytes((*c, 255))) * k
        rows.extend(bytearray(row) for _ in range(k))
    return rows


def render_sprite(cid: str, width: int, height: int) -> list[bytearray]:
    """512×1280(격자 128×320 × 정수 배율) RGBA 행 목록. 비율·배율이 맞지 않으면 ValueError."""
    if width <= 0 or width % GRID or height * GRID != width * SPRITE_ROWS:
        raise ValueError(f"sprite 크기는 {GRID}×{SPRITE_ROWS}의 정수배여야 함: {width}x{height}")
    return _scale_rows(draw(cid).px, width // GRID)


def crop_portrait(width: int, rows: list[bytes]) -> list[bytearray]:
    """sprite RGBA 행(가로 `width`) → 위 `width`행을 자른 정사각 RGB 초상. 먹남 위에 알파 합성한다.

    반투명(8비트 알파)도 받는다: 색 = round((c·a + 먹남·(255 − a)) / 255). 생성 도구가 만든 실제 sprite에도 쓴다.
    """
    if len(rows) < width or any(len(r) != width * 4 for r in rows[:width]):
        raise ValueError("sprite 행이 초상 크기보다 작거나 RGBA가 아님")
    bg = bytes(BG)
    out: list[bytearray] = []
    for r in rows[:width]:
        r = bytes(r)
        alpha = r[3::4]
        line = bytearray(width * 3)
        line[0::3], line[1::3], line[2::3] = r[0::4], r[1::4], r[2::4]
        for x, a in enumerate(alpha):
            if a == 255:
                continue
            if a == 0:
                line[x * 3:x * 3 + 3] = bg
            else:
                line[x * 3:x * 3 + 3] = bytes((r[x * 4 + c] * a + bg[c] * (255 - a) + 127) // 255 for c in range(3))
        out.append(line)
    return out


def render(cid: str, width: int, height: int) -> list[bytearray]:
    """512×512 RGB 초상 = sprite 위 512×512 크롭 + 먹남 알파 합성. 크기가 128의 정수배 정사각형이 아니면 ValueError."""
    if width != height or width % GRID or width <= 0:
        raise ValueError(f"초상 크기는 {GRID}의 정수배 정사각형이어야 함: {width}x{height}")
    return crop_portrait(width, render_sprite(cid, width, width * SPRITE_ROWS // GRID))
