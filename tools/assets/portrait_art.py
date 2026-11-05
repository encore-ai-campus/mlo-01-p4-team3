"""임시 sprite·초상 그리기(도메인). 회색 사람 실루엣 + 기준선 + 이름표. 크기·기준선·배치 확인용이며 최종 그림이 아니다.

- sprite: 사람마다 전신 1장, 512×1280 RGBA(바탕 투명). 회색 실루엣(머리·목·몸통·팔·다리·발)과 진한 회색 윤곽,
  눈높이 y=200(`EYE_Y`)·허리선 y=640(`WAIST_Y`) 점선, 발끝 y=1248(`FOOT_Y`). 가로 가운데 정렬.
- 머리 장식(`HEADGEAR`: 갓·쪽머리·상투·삿갓·벙거지)만 사람마다 다르고 나머지 실루엣은 모두 같다. 모르는 id는 장식 없음.
- 이름표: 몸통(허리 위, 평소 표시 범위 0~640행 안)에 2줄 "임시" / 표시 이름. 이름은 호출 쪽이 공개 표시 이름(`name_ko`,
  무당은 `HERO_LABELS`)으로 넘긴다. 비트맵에 없는 글자가 있으면 이름 대신 id를 쓴다(`glyphs.fits`).
  역할·진영 같은 게임 정보는 쓰지 않는다.
- 표정 변형(`expression` 이름을 주면): 기본과 같은 그림에 얼굴 영역(`vn.FACE_BOX`) 안에만 표정 이름을 찍는다. 얼굴 밖 픽셀은
  기본 sprite와 같다(표정 변형 검사 `vn.check_expression`과 같은 규칙).
- 초상: sprite 위 512×512를 잘라 먹남 위에 알파 합성한다(`crop_portrait`, 실제 sprite에도 같은 함수를 쓴다).
- 같은 (id, 이름, 크기)면 같은 픽셀이다.
"""
from __future__ import annotations

from tools.assets import glyphs

RGB = tuple[int, int, int]
W, H = 512, 1280               # sprite 크기(spec.md 2절)
EYE_Y = 200                    # 눈높이(px). spec.md 6절 기준선과 같다
WAIST_Y = 640                  # 허리선(px). 평소 표시는 0~640행
FOOT_Y = 1248                  # 발끝(px)
CX = W // 2

BG: RGB = (0x2E, 0x34, 0x4A)   # 먹남: 초상의 투명 부분을 채우는 색
FILL: RGB = (0x9A, 0x9A, 0x9A)
EDGE: RGB = (0x5E, 0x5E, 0x5E)
GUIDE: RGB = (0xC0, 0x48, 0x40)
TEXT: RGB = (0xF4, 0xF4, 0xF4)
EDGE_PX = 3
LABEL_PREFIX = "임시"
HERO_LABELS = {"mudang_f": "무당(여)", "mudang_m": "무당(남)"}
LABEL_Y = 380                  # 이름표 첫 줄 위(px). 두 줄 모두 초상 크롭(0~511)과 평소 표시(0~639) 안
EXPR_Y = 222                   # 표정 이름 위(px). 얼굴 영역 vn.FACE_BOX(y 104~311) 안
EXPR_TEXT: RGB = (0x30, 0x30, 0x30)

# 머리 장식: 공개 id → 종류. 실루엣 구분용 단순 도형일 뿐 역할과 무관하다.
HEADGEAR: dict[str, str] = {
    "mudang_f": "bun", "mudang_m": "gat", "hunjang": "gat", "jumo": "bun", "gwabu": "bun", "gisaeng": "bun",
    "boy": "topknot", "meosum": "topknot", "blacksmith": "topknot", "ferryman": "straw", "yakcho": "straw",
    "hunter": "cap", "monk": None,
}

Mask = list[int]               # 행마다 W비트 정수(최상위 비트 = x 0)


def _span(x0: float, x1: float) -> int:
    a, b = max(0, round(x0)), min(W, round(x1))
    return 0 if a >= b else ((1 << (b - a)) - 1) << (W - b)


def _rect(m: Mask, x0: float, y0: int, x1: float, y1: int) -> None:
    s = _span(x0, x1)
    for y in range(max(0, y0), min(H, y1)):
        m[y] |= s


def _ellipse(m: Mask, cx: float, cy: float, rx: float, ry: float) -> None:
    for y in range(max(0, int(cy - ry)), min(H, int(cy + ry) + 1)):
        t = (y + 0.5 - cy) / ry
        if abs(t) <= 1:
            half = rx * (1 - t * t) ** 0.5
            m[y] |= _span(cx - half, cx + half)


def _trapezoid(m: Mask, y0: int, y1: int, half0: float, half1: float) -> None:
    for y in range(max(0, y0), min(H, y1)):
        t = (y - y0) / max(1, y1 - y0)
        half = half0 + (half1 - half0) * t
        m[y] |= _span(CX - half, CX + half)


def body_mask(cid: str) -> Mask:
    """실루엣 마스크(장식 포함)."""
    m: Mask = [0] * H
    _ellipse(m, CX, EYE_Y, 62, 80)                    # 머리: 눈높이가 머리 중심
    _rect(m, CX - 24, 270, CX + 24, 310)              # 목
    _ellipse(m, CX, 340, 130, 46)                     # 어깨
    _trapezoid(m, 330, WAIST_Y + 10, 124, 104)        # 몸통(허리까지)
    _trapezoid(m, WAIST_Y, 700, 104, 112)             # 엉덩이
    for side in (-1, 1):
        _trapezoid_side(m, side)
        _rect(m, CX + side * 54 - 34, 690, CX + side * 54 + 34, FOOT_Y - 24)   # 다리
        _ellipse(m, CX + side * 62, FOOT_Y - 20, 46, 20)                      # 발(아랫변 = FOOT_Y)
    gear = HEADGEAR.get(cid)
    if gear == "gat":
        _ellipse(m, CX, 130, 130, 14)                 # 챙
        _rect(m, CX - 44, 60, CX + 44, 130)           # 모정
    elif gear == "bun":
        _ellipse(m, CX, 110, 40, 30)                  # 쪽머리(위)
    elif gear == "topknot":
        _ellipse(m, CX, 112, 18, 22)                  # 상투
    elif gear == "straw":
        _trapezoid(m, 70, 150, 20, 140)               # 삿갓(원뿔)
    elif gear == "cap":
        _ellipse(m, CX, 135, 72, 40)                  # 벙거지
    return m


def _trapezoid_side(m: Mask, side: int) -> None:
    """팔: 어깨에서 허리 아래로 조금 벌어진 막대."""
    for y in range(330, 700):
        t = (y - 330) / 370
        x = CX + side * (128 + 14 * t)
        m[y] |= _span(x - 26, x + 26)


def _erode(m: Mask, r: int) -> Mask:
    full = (1 << W) - 1
    out = list(m)
    for _ in range(r):
        h = [(row & (row << 1) & (row >> 1)) & full for row in out]
        out = [h[y] & (h[y - 1] if y else 0) & (h[y + 1] if y + 1 < H else 0) for y in range(H)]
    return out


def label_for(cid: str, name: str | None) -> str:
    """이름표 둘째 줄: 비트맵이 있으면 표시 이름, 없으면 id."""
    text = HERO_LABELS.get(cid) or name or cid
    return text if glyphs.fits(text) else cid


def render_sprite(cid: str, width: int, height: int, name: str | None = None,
                  expression: str | None = None) -> list[bytearray]:
    """512×1280 RGBA 행 목록. `cid`는 인물 id(표정 접미사 없음), `expression`은 표정 이름(기본이면 None). 크기가 다르면 ValueError."""
    if (width, height) != (W, H):
        raise ValueError(f"sprite 크기는 {W}x{H}여야 함: {width}x{height}")
    m = body_mask(cid)
    inner = _erode(m, EDGE_PX)
    fill_px, edge_px = bytes((*FILL, 255)), bytes((*EDGE, 255))
    guide_px = bytes((*GUIDE, 255))
    rows: list[bytearray] = []
    for y in range(H):
        row, inn = m[y], inner[y]
        line = bytearray(W * 4)
        if row:
            bits = bin(row)[2:].zfill(W)
            ibits = bin(inn)[2:].zfill(W)
            for x in range(W):
                if bits[x] == "1":
                    line[x * 4:x * 4 + 4] = fill_px if ibits[x] == "1" else edge_px
        if y in (EYE_Y, WAIST_Y):                      # 기준선: 실루엣 위에만 점선(8px 칠하고 8px 비움)
            for x in range(W):
                if line[x * 4 + 3] and (x // 8) % 2 == 0:
                    line[x * 4:x * 4 + 4] = guide_px
        rows.append(line)
    second = label_for(cid, name)
    for i, text in enumerate((LABEL_PREFIX, second)):
        x = CX - glyphs.width(text) // 2
        glyphs.draw(rows, 4, x, LABEL_Y + i * (glyphs.HEIGHT + 6), text, TEXT)
    if expression:
        if not glyphs.fits(expression):
            raise ValueError(f"표정 이름 글자 비트맵 없음: {expression!r} (build_glyphs 다시 실행)")
        glyphs.draw(rows, 4, CX - glyphs.width(expression) // 2, EXPR_Y, expression, EXPR_TEXT)
    return rows


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


def render(cid: str, width: int, height: int, name: str | None = None,
           expression: str | None = None) -> list[bytearray]:
    """512×512 RGB 초상 = sprite 위 512×512 크롭 + 먹남 알파 합성. 크기가 512 정사각형이 아니면 ValueError."""
    if (width, height) != (W, W):
        raise ValueError(f"초상 크기는 {W}x{W}여야 함: {width}x{height}")
    return crop_portrait(width, render_sprite(cid, W, H, name, expression))
