"""임시 초상 그리기(도메인). Claude가 코드로 그린 도트 그림: 128×128 격자에 직접 래스터화 → ×4 최근접 확대.

- 색은 스타일 가이드 3절 17색(`PALETTE`)만 쓴다. 안티에일리어싱이 없어 확대 후 4×4 블록이 모두 단색이다.
- 좌표는 512 기준으로 적고 격자(÷4)로 바꿔 칠한다. 픽셀 중심이 도형 안이면 칠한다(스캔라인, 짝홀 규칙).
- 외곽선: 도형마다 경계 픽셀(4이웃 중 도형 밖이 있는 픽셀)을 먹색 1픽셀로 칠한다. 캔버스 가장자리는 경계로 치지 않는다.
- 그리는 순서는 뒤 → 앞(뒷머리 → 옷 → 목·귀 → 얼굴 → 눈코입 → 앞머리·옆머리 → 모자). 머리카락이 눈썹·눈을 덮는다.
- 인물은 `CHARACTERS` 데이터(머리 모양·모자·옷·소품)로 정한다. 얼굴 틀(머리 크기·눈높이·눈 모양)은 모두 같다.
  주인공(무당 2종)만 눈 하이라이트 2점, 주민은 전원 1점(스타일 가이드 4절 판정표). 해시나 게임 규칙으로 모양을 고르지 않는다.
- 모르는 id는 `DEFAULT_RESIDENT`(평범한 주민)로 그린다. 같은 입력이면 같은 픽셀이다.
"""
from __future__ import annotations

import math
import re

RGB = tuple[int, int, int]
GRID = 128
SRC = 512                      # 도형 좌표의 기준 크기
UNIT = SRC / GRID              # 512 좌표 → 격자 1칸

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

# 눈 영역(격자 좌표, [x0, x1) × [y0, y1)). 테스트가 하이라이트 수를 센다. 눈썹(y≈54)은 들어가지 않는다.
EYE_BOXES: tuple[tuple[int, int, int, int], ...] = ((44, 58, 62, 70), (66, 58, 84, 70))


# ---------------------------------------------------------------- 래스터화

class Grid:
    """128×128 색 격자."""

    def __init__(self) -> None:
        self.px: list[list[RGB]] = [[BG] * GRID for _ in range(GRID)]

    def _paint(self, mask: set[tuple[int, int]], color: RGB, outline: bool) -> None:
        for x, y in mask:
            self.px[y][x] = color
        if not outline:
            return
        for x, y in mask:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < GRID and 0 <= ny < GRID and (nx, ny) not in mask:
                    self.px[y][x] = INK
                    break

    def fill(self, pts: list[tuple[float, float]], color: RGB, outline: bool = True) -> None:
        """다각형(512 좌표)을 칠한다. 픽셀 중심 기준, 짝홀 규칙."""
        g = [(x / UNIT, y / UNIT) for x, y in pts]
        ys = [p[1] for p in g]
        mask: set[tuple[int, int]] = set()
        n = len(g)
        for y in range(max(0, int(min(ys))), min(GRID, int(max(ys)) + 1)):
            yc = y + 0.5
            xs = []
            for i in range(n):
                (x0, y0), (x1, y1) = g[i], g[(i + 1) % n]
                if (y0 <= yc < y1) or (y1 <= yc < y0):
                    xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for a, b in zip(xs[0::2], xs[1::2]):
                for x in range(max(0, math.ceil(a - 0.5)), min(GRID, math.ceil(b - 0.5))):
                    mask.add((x, y))
        self._paint(mask, color, outline)

    def line(self, pts: list[tuple[float, float]], width: float, color: RGB) -> None:
        """굵은 선(512 좌표, 굵기도 512 기준). 선을 따라 원을 찍는다."""
        r = max(width / UNIT / 2, 0.5)
        mask: set[tuple[int, int]] = set()
        g = [(x / UNIT, y / UNIT) for x, y in pts]
        for (x0, y0), (x1, y1) in zip(g, g[1:] or g):
            steps = max(1, int(math.hypot(x1 - x0, y1 - y0) * 4))
            for i in range(steps + 1):
                cx, cy = x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps
                for y in range(int(cy - r) - 1, int(cy + r) + 2):
                    for x in range(int(cx - r) - 1, int(cx + r) + 2):
                        if 0 <= x < GRID and 0 <= y < GRID and (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                            mask.add((x, y))
        self._paint(mask, color, False)

    def block(self, x0: float, y0: float, x1: float, y1: float, color: RGB) -> None:
        """격자 좌표 사각형 [x0, x1) × [y0, y1)을 칠한다(하이라이트 점처럼 픽셀을 정확히 놓을 때)."""
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                if 0 <= x < GRID and 0 <= y < GRID:
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

# hair: 머리 모양, hat: 모자, cloth: 저고리(기본색, 그림자색, 깃), outer: 겉옷, extra: 소품
CHARACTERS: dict[str, dict] = {
    "mudang_f": {"hero": True, "sex": "f", "hair": "bangs_long", "hat": None,
                 "cloth": (WHITE, WHITE_S, HANJI), "outer": ("vest", RED, RED_S), "extra": ["binyeo"]},
    "mudang_m": {"hero": True, "sex": "m", "hair": "bangs_short", "hat": None,
                 "cloth": (WHITE, WHITE_S, HANJI), "outer": ("vest", RED, RED_S), "extra": []},
    "hunjang": {"sex": "m", "hair": "pulled", "hat": "gat",
                "cloth": (WHITE, WHITE_S, HANJI), "outer": None, "extra": ["beard"]},
    "jumo": {"sex": "f", "hair": "part_bun", "hat": None,
             "cloth": (BLUE, BLUE_S, HANJI), "outer": None, "extra": ["binyeo", "goreum_red", "apron"]},
    "blacksmith": {"sex": "m", "hair": "pulled", "hat": "dugeon",
                   "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("apron", NIGHT, NIGHT), "extra": []},
    "meosum": {"sex": "m", "hair": "pulled", "hat": "headband",
               "cloth": (HANJI, WHITE_S, WHITE_S), "outer": None, "extra": []},
    "yakcho": {"sex": "m", "hair": "pulled", "hat": "towel",
               "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("vest", GREEN, GREEN), "extra": []},
    "gwabu": {"sex": "f", "hair": "part_bun", "hat": None,
              "cloth": (WHITE, WHITE_S, WHITE_S), "outer": None, "extra": ["binyeo_wood"]},
    "ferryman": {"sex": "m", "hair": "pulled", "hat": "paeraengi",
                 "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("vest", BLUE, BLUE_S), "extra": []},
    "gisaeng": {"sex": "f", "hair": "gache", "hat": None,
                "cloth": (GOLD, GOLD_S, HANJI), "outer": None, "extra": ["goreum_red"]},
    "hunter": {"sex": "m", "hair": "pulled", "hat": "fur",
               "cloth": (HANJI, WHITE_S, WHITE_S), "outer": ("vest", GOLD_S, GOLD_S), "extra": []},
    "monk": {"sex": "m", "hair": "bald", "hat": None,
             "cloth": (NIGHT, NIGHT, HANJI), "outer": None, "extra": ["kasa"]},
    "boy": {"sex": "m", "hair": "braid", "hat": None,
            "cloth": (HANJI, WHITE_S, WHITE_S), "outer": None, "extra": []},
}
DEFAULT_RESIDENT: dict = {"sex": "m", "hair": "pulled", "hat": None,
                          "cloth": (HANJI, WHITE_S, WHITE_S), "outer": None, "extra": []}


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


def _clothes(g: Grid, ch: dict) -> None:
    base, shade, collar = ch["cloth"]
    g.fill(path("M84 512 C92 438 160 404 256 400 C352 404 420 438 428 512 Z"), base)
    g.fill(path("M300 404 C370 412 418 444 428 512 L360 512 C356 460 334 424 300 412 Z"), shade, False)
    outer = ch["outer"]
    if outer and outer[0] == "vest":
        g.fill(path("M118 474 C130 444 168 424 202 418 L238 512 L110 512 Z"), outer[1])
        g.fill(path("M394 474 C382 444 344 424 310 418 L274 512 L402 512 Z"), outer[2])
    if outer and outer[0] == "apron":
        g.fill(path("M190 440 L322 440 L330 512 L182 512 Z"), outer[1])
        g.line(path("M196 444 L170 410"), 10, outer[1])
        g.line(path("M316 444 L342 410"), 10, outer[1])
    if "apron" in ch["extra"]:
        g.fill(path("M196 470 L316 470 L322 512 L190 512 Z"), WHITE)
    if "kasa" in ch["extra"]:
        g.fill(path("M150 430 L200 414 L330 512 L262 512 Z"), RED_S)
    g.fill(path("M234 330 L234 412 L278 412 L278 330 Z"), SKIN_S)
    g.fill(path("M206 404 L256 474 L306 404 L290 400 L256 450 L222 400 Z"), collar)
    if "goreum_red" in ch["extra"]:
        g.fill(path("M262 462 L300 470 L292 500 L266 478 Z"), RED)
        g.fill(path("M260 466 L276 512 L258 512 L252 470 Z"), RED)


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
    """인물 하나를 128 격자에 그린다."""
    ch = character(cid)
    g = Grid()
    _back_hair(g, ch)
    _clothes(g, ch)
    _ears(g, ch)
    _face(g, ch)
    _front_hair(g, ch)
    _hat(g, ch)
    return g


def render(cid: str, width: int, height: int) -> list[bytearray]:
    """512×512(격자 128 × 정수 배율) RGB 행 목록. 크기가 격자의 정수배가 아니면 ValueError."""
    if width != height or width % GRID or width <= 0:
        raise ValueError(f"초상 크기는 {GRID}의 정수배 정사각형이어야 함: {width}x{height}")
    k = width // GRID
    g = draw(cid)
    rows: list[bytearray] = []
    for line in g.px:
        row = bytearray()
        for c in line:
            row += bytes(c) * k
        rows.extend(bytearray(row) for _ in range(k))
    return rows
