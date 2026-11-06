"""임시 sprite·초상 그리기(도메인). 회색 사람 실루엣 + 기준선 + 이름표. 크기·기준선·배치 확인용이며 최종 그림이 아니다.

- sprite: 사람마다 전신 1장, 512×1280 RGBA(바탕 투명). 회색 실루엣(머리·목·어깨·몸통·팔·다리·발)과 진한 회색 윤곽,
  발끝 y=1248(`FOOT_Y`), 가로 가운데 정렬. 키는 인물의 키 등급(`vn.grade_for`: 머리 꼭대기 y·등신)대로 그리고, 모든 부위를
  머리 높이 h에 비례해 같은 축척으로 그린다(머리 타원 가로 반지름 0.39h, 어깨 꼭대기+1.3h, 허리 +3.1h, 엉덩이 +3.6h).
  점선 기준선은 눈 y(등급마다 다름)와 평소 표시 경계 y=640(`vn.NORMAL_ROWS`)이다.
- 머리 장식(`HEADGEAR`: 갓·쪽머리·상투·삿갓·벙거지)은 사람마다 다르고 머리 크기에 비례한다. 모르는 id는 장식 없음.
  장식이 머리 위 여백(`vn.TOP_MARGIN_MIN`)을 넘으면 그 위는 그리지 않는다(안전장치. 지금 등급 값에서는 잘리는 장식이 없다).
- 이름표: 어깨 아래(평소 표시 0~639행과 그 인물의 초상 크롭 안, 얼굴 영역 밖)에 2줄 "임시" / 표시 이름. 이름은 호출 쪽이
  공개 표시 이름(`name_ko`, 무당은 `HERO_LABELS`)으로 넘긴다. 비트맵에 없는 글자가 있으면 이름 대신 id를 쓴다(`glyphs.fits`).
  역할·진영 같은 게임 정보는 쓰지 않는다.
- 표정 변형(`expression` 이름을 주면): 기본과 같은 그림에 얼굴 영역(`vn.face_box`) 안에만 표정 이름을 찍는다. 얼굴 밖 픽셀은
  기본 sprite와 같다(표정 변형 검사 `vn.check_expression`과 같은 규칙).
- 초상: sprite에서 `vn.portrait_top(인물 id)`부터 512행을 잘라 먹남 위에 알파 합성한다(`crop_portrait`, 실제 sprite에도 같은
  함수를 쓴다).
- 같은 (id, 이름, 크기)면 같은 픽셀이다.
"""
from __future__ import annotations

from tools.assets import glyphs, vn

RGB = tuple[int, int, int]
W, H = 512, 1280               # sprite 크기(spec.md 2절)
FOOT_Y = vn.FOOT_Y             # 발끝(px)
CX = W // 2

BG: RGB = (0x2E, 0x34, 0x4A)   # 먹남: 초상의 투명 부분을 채우는 색
FILL: RGB = (0x9A, 0x9A, 0x9A)
EDGE: RGB = (0x5E, 0x5E, 0x5E)
GUIDE: RGB = (0xC0, 0x48, 0x40)
TEXT: RGB = (0xF4, 0xF4, 0xF4)
EDGE_PX = 3
LABEL_PREFIX = "임시"
HERO_LABELS = {"mudang_f": "무당(여)", "mudang_m": "무당(남)"}
LABEL_GAP = 6                  # 이름표 두 줄 사이(px)
LABEL_FLOOR = 4                # 이름표 아래와 평소 표시 경계(640) 사이 최소 간격(px)
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
    """실루엣 마스크(장식 포함). 키 등급(`vn.grade_for`)의 머리 꼭대기·머리 높이로 그린다."""
    g = vn.grade_for(cid)
    t, h = g.top, g.head
    m: Mask = [0] * H
    _ellipse(m, CX, t + h / 2, 0.39 * h, h / 2)                        # 머리
    _rect(m, CX - 0.16 * h, round(t + 0.9 * h), CX + 0.16 * h, round(t + 1.35 * h))   # 목
    sh, waist, hip = round(t + 1.3 * h), round(t + 3.1 * h), round(t + 3.6 * h)
    _ellipse(m, CX, sh + 0.06 * h, 0.81 * h, 0.29 * h)               # 어깨
    _trapezoid(m, sh, waist, 0.78 * h, 0.62 * h)                       # 몸통(허리까지)
    _trapezoid(m, waist, hip, 0.62 * h, 0.7 * h)                       # 엉덩이
    for side in (-1, 1):
        _arm(m, side, sh, round(t + 3.9 * h), h)
        _rect(m, CX + side * 0.34 * h - 0.21 * h, hip - 2, CX + side * 0.34 * h + 0.21 * h, round(FOOT_Y - 0.15 * h))  # 다리
        _ellipse(m, CX + side * 0.39 * h, FOOT_Y - 0.12 * h, 0.29 * h, 0.12 * h)              # 발(아랫변 = FOOT_Y)
    gear = HEADGEAR.get(cid)
    if gear == "gat":
        _ellipse(m, CX, t + 0.0625 * h, 0.8125 * h, 0.0875 * h)       # 챙
        _rect(m, CX - 0.275 * h, round(t - 0.375 * h), CX + 0.275 * h, round(t + 0.0625 * h))  # 모정
    elif gear == "bun":
        _ellipse(m, CX, t - 0.0625 * h, 0.25 * h, 0.1875 * h)         # 쪽머리(위)
    elif gear == "topknot":
        _ellipse(m, CX, t - 0.05 * h, 0.1125 * h, 0.1375 * h)         # 상투
    elif gear == "straw":
        _trapezoid(m, round(t - 0.3125 * h), round(t + 0.1875 * h), 0.125 * h, 0.875 * h)  # 삿갓(원뿔)
    elif gear == "cap":
        _ellipse(m, CX, t + 0.09375 * h, 0.45 * h, 0.25 * h)          # 벙거지
    for y in range(vn.TOP_MARGIN_MIN):                                 # 위 여백 안은 비운다(큰 키의 장식)
        m[y] = 0
    return m


def _arm(m: Mask, side: int, y0: int, y1: int, h: float) -> None:
    """팔: 어깨에서 허리 아래로 조금 벌어진 막대."""
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0)
        x = CX + side * (0.8 * h + 0.09 * h * t)
        m[y] |= _span(x - 0.16 * h, x + 0.16 * h)


def eye_y(cid: str) -> int:
    """눈 기준선 y(정수)."""
    return vn._round(vn.grade_for(cid).eye_y)


def label_y(cid: str) -> int:
    """이름표 첫 줄 위 y: 어깨 아래. 두 줄 끝이 평소 표시 경계(640)보다 LABEL_FLOOR 이상 위."""
    g = vn.grade_for(cid)
    return min(round(g.top + 1.45 * g.head), vn.NORMAL_ROWS - (2 * glyphs.HEIGHT + LABEL_GAP) - LABEL_FLOOR)


def expr_y(cid: str) -> int:
    """표정 이름 위 y: 눈 조금 아래(얼굴 영역 안)."""
    g = vn.grade_for(cid)
    return round(g.eye_y + 0.14 * g.head)


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
    guides = (eye_y(cid), vn.NORMAL_ROWS)
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
        if y in guides:                                # 기준선: 실루엣 위에만 점선(8px 칠하고 8px 비움)
            for x in range(W):
                if line[x * 4 + 3] and (x // 8) % 2 == 0:
                    line[x * 4:x * 4 + 4] = guide_px
        rows.append(line)
    second = label_for(cid, name)
    for i, text in enumerate((LABEL_PREFIX, second)):
        x = CX - glyphs.width(text) // 2
        glyphs.draw(rows, 4, x, label_y(cid) + i * (glyphs.HEIGHT + LABEL_GAP), text, TEXT)
    if expression:
        if not glyphs.fits(expression):
            raise ValueError(f"표정 이름 글자 비트맵 없음: {expression!r} (build_glyphs 다시 실행)")
        glyphs.draw(rows, 4, CX - glyphs.width(expression) // 2, expr_y(cid), expression, EXPR_TEXT)
    return rows


def crop_portrait(width: int, rows: list[bytes], top: int) -> list[bytearray]:
    """sprite RGBA 행(가로 `width`) → `top`행부터 `width`행을 자른 정사각 RGB 초상. 먹남 위에 알파 합성한다.
    `top`은 그 인물의 `vn.portrait_top(인물 id)`다.

    반투명(8비트 알파)도 받는다: 색 = round((c·a + 먹남·(255 − a)) / 255). 생성 도구가 만든 실제 sprite에도 쓴다.
    """
    if top < 0 or len(rows) < top + width or any(len(r) != width * 4 for r in rows[top:top + width]):
        raise ValueError("sprite 행이 초상 크롭 범위보다 작거나 RGBA가 아님")
    bg = bytes(BG)
    out: list[bytearray] = []
    for r in rows[top:top + width]:
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
    """512×512 RGB 초상 = sprite의 `vn.portrait_top(cid)`부터 512×512 크롭 + 먹남 알파 합성. 크기가 512 정사각형이 아니면 ValueError."""
    if (width, height) != (W, W):
        raise ValueError(f"초상 크기는 {W}x{W}여야 함: {width}x{height}")
    return crop_portrait(width, render_sprite(cid, W, H, name, expression), vn.portrait_top(cid))
