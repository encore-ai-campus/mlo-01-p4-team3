"""비주얼 노벨 화면용 에셋 규칙(도메인): sprite·장면 배경 검사, 화면 배치 수치, 검수용 합성. 파일·OS를 건드리지 않는다.

화풍은 반실사 일러스트(11/5 PM 결정)라 색 수·반투명은 검사하지 않는다(사람 검수, 스타일 가이드 체크리스트).

- sprite(사람마다 전신 1장): 512×1280 RGBA, 8비트 알파. 보이는 픽셀(알파 ≥ 16, 잔여 노이즈 무시)의 경계 상자 가로 가운데가 256±16px,
  머리 위 여백 16px 이상, 발끝 아래 여백 16~48px(발끝 기준선 y=1248).
- 키 등급(11/6 PM 결정): 모든 인물을 같은 축척으로 그리고 키는 인물마다 다르다. 인물 id → 등급(`CHARACTER_GRADE`),
  등급 → 머리 꼭대기 y·등신(`HEIGHT_GRADES`). 머리 높이 h = (1248 − 꼭대기) / 등신, 눈 y = 꼭대기 + h/2.
  머리 꼭대기 위치는 그림에서 자동으로 찾을 수 없어 사람 검수 항목이다(spec.md 6절). 등급은 역할과 무관한 고정 표다.
- 표정 변형(11/5 PM 결정, 크로스체크 19): 기본 sprite에서 얼굴 영역(`face_box(인물 id)`, 등급에서 계산)만 바꾼다. 편집 결과의 얼굴 영역만 기본에
  붙여 넣으므로 얼굴 밖 픽셀은 기본과 정확히 같아야 한다(완전 투명 픽셀은 RGB를 보지 않는다). 얼굴 영역이 기본과 완전히
  같으면(표정 변화 없음) 위반이다. 기본 sprite가 바뀐 것은 manifest `edited_from_sha256`으로 잡는다(make_placeholders).
- 마스코트: 512×512 RGBA, 보이는 픽셀이 있고 네 모서리와 전체의 `MASCOT_CLEAR_MIN` 이상이 투명(바탕 투명).
  sprite와 같은 판정으로 보이는 픽셀 경계 상자의 가로 가운데 256±16px, 아래 여백 16~48px(발끝 기준선 y=480,
  11/6 PM 결정: 화면에서 대사창 위에 떠 보이지 않게).
- 장면 배경: 1280×720, 완전 불투명(알파가 있으면 모두 255).
- 초상 크롭 위 y(`portrait_top`): 눈이 초상 안 등급별 y(`HeightGrade.portrait_eye`)에 오도록
  clamp(round(눈 y − portrait_eye), 0, 1280 − 512).
- 배치(B-10-07 기준, 무대 1280×720, 대사창 아래 180px):
  - 평소(`normal`): sprite 0~640행(인물과 무관한 고정 범위. 키가 작은 인물은 낮게 선다)을 부드럽게 0.8배 → 410×512, 가운데, 위 y=28·아랫변 y=540(대사창 윗변).
  - 전신 컷(`full`): sprite 전체를 0.5배 → 256×640, 가운데, 위 y=40. 컷 동안 대사창을 숨긴다.
  - 밤 화면에는 sprite가 없다. 밤 습격 결과 전신 컷은 아침 회의 화면에서 보인다.
  - 마스코트(`MASCOT`): 0.5배 → 256×256, 무대 왼쪽(x 64), 위 y 260, 아랫변 y 516(대사창 위). 까치는 아침 희생자 발표,
    족제비는 안내(튜토리얼·도움말)에만 나온다.

검사 함수는 문제 목록(문자열)을 돌려준다. 빈 목록이면 통과.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

SPRITE_SIZE = (512, 1280)
BACKGROUND_SIZE = (1280, 720)
NORMAL_ROWS = 640                          # 평소 표시에 쓰는 sprite 행 수(0~639, 모든 인물 같음)
FOOT_Y = 1248                              # 발끝 기준선(px)
CENTER_TOLERANCE = 16                      # 경계 상자 가운데 허용 오차(px)
TOP_MARGIN_MIN = 16                        # 머리 위 최소 여백(px)
BOTTOM_MARGIN = (16, 48)                   # 발끝 아래 여백(px). 기준선 1248 ± 16

STAGE = (1280, 720)                        # 무대(배경) 크기
DIALOGUE_HEIGHT = 180                      # 대사창 높이(무대 아래 1/4을 덮음)
NIGHT_SPRITES = 0                          # 밤 장면 sprite 수(밤 실행자 노출 금지)

PORTRAIT_SIZE = 512                        # 초상 한 변(px)
MASCOT_SIZE = (512, 512)
MASCOT_CLEAR_MIN = 0.2                     # 마스코트 전체에서 투명(알파 < 16) 픽셀 비율 하한
MASCOT = (64, 260, (256, 256))             # 무대 안 (x0, y0, 축소 크기)


@dataclass(frozen=True)
class Mode:
    """sprite 표시 방식: 잘라 쓸 행 범위, 축소 후 크기, 무대 안 위쪽 y, 대사창 표시 여부."""
    name: str
    rows: tuple[int, int]
    size: tuple[int, int]
    top: int
    dialogue: bool


NORMAL = Mode("normal", (0, NORMAL_ROWS), (410, 512), 28, True)       # 아랫변 28 + 512 = 540 = 대사창 윗변
FULL = Mode("full", (0, SPRITE_SIZE[1]), (256, 640), 40, False)   # 전신 컷(첫 등장·추방/처형 결과·엔딩·밤 습격 결과)
MODES = {m.name: m for m in (NORMAL, FULL)}

@dataclass(frozen=True)
class HeightGrade:
    """키 등급: 머리 꼭대기 y(px), 등신, 초상 안 눈 위치 y(px). 발끝은 모두 FOOT_Y."""
    name: str
    top: int
    heads: float
    portrait_eye: int

    @property
    def head(self) -> float:
        """머리 높이(px)."""
        return (FOOT_Y - self.top) / self.heads

    @property
    def eye_y(self) -> float:
        """눈 y(px) = 머리 꼭대기 + 머리 높이 / 2."""
        return self.top + self.head / 2


HEIGHT_GRADES = {g.name: g for g in (
    HeightGrade("tall", 24, 7.2, 200),     # 큰 키
    HeightGrade("male", 94, 7.0, 200),     # 남성 보통
    HeightGrade("female", 185, 6.8, 200),  # 여성 보통
    HeightGrade("child", 339, 6.0, 230),   # 아이(초상에서 여성보다 살짝 아래)
)}
# 공개 인물 id → 키 등급(11/6 PM 결정). 역할·성격과 무관하다. 새 인물은 여기에 등급을 넣어야 그릴 수 있다.
CHARACTER_GRADE = {
    "boy": "child",
    "jumo": "female", "gwabu": "female", "gisaeng": "female", "mudang_f": "female",
    "hunjang": "male", "meosum": "male", "yakcho": "male", "ferryman": "male", "monk": "male", "mudang_m": "male",
    "blacksmith": "tall", "hunter": "tall",
}


def _round(v: float) -> int:
    """0.5는 올리는 정수 반올림(파이썬 round의 짝수 반올림을 쓰지 않는다)."""
    return math.floor(v + 0.5)


def grade_for(cid: str) -> HeightGrade:
    """인물 id(표정 접미사 없음)의 키 등급. 표에 없으면 ValueError."""
    try:
        return HEIGHT_GRADES[CHARACTER_GRADE[cid]]
    except KeyError:
        raise ValueError(f"키 등급이 없는 인물 id: {cid!r} (vn.CHARACTER_GRADE에 추가)") from None


def face_box(cid: str) -> tuple[int, int, int, int]:
    """표정 변형에서 바꿔도 되는 얼굴 영역 (x0, y0, x1, y1), 끝 미포함.
    x 256 ± 0.6h, y (눈 − 0.6h) ~ (눈 + 0.69h), 정수 반올림(h = 머리 높이)."""
    g = grade_for(cid)
    cx = SPRITE_SIZE[0] / 2
    return (_round(cx - 0.6 * g.head), _round(g.eye_y - 0.6 * g.head),
            _round(cx + 0.6 * g.head), _round(g.eye_y + 0.69 * g.head))


def portrait_top(cid: str) -> int:
    """초상 크롭(512×512)의 sprite 안 위 y. 눈이 초상 안 등급별 y(`portrait_eye`)에 오게 하고 sprite 밖으로 나가지 않게 자른다."""
    g = grade_for(cid)
    top = _round(g.eye_y - g.portrait_eye)
    return min(SPRITE_SIZE[1] - PORTRAIT_SIZE, max(0, top))


VISIBLE_ALPHA = 16                         # 경계 상자에 넣는 알파 하한(배경 제거 뒤 남은 옅은 점 무시)
_VISIBLE = bytes(0 if v < VISIBLE_ALPHA else 1 for v in range(256))
_NUL = bytes(1)
_FF = bytes((255,))


# ---------------------------------------------------------------- 검사
def visible_box(width: int, rows: Sequence[bytes]) -> Optional[tuple[int, int, int, int]]:
    """RGBA 행에서 보이는 픽셀(알파 ≥ VISIBLE_ALPHA)의 경계 상자 (x0, y0, x1, y1), 끝 미포함. 없으면 None."""
    x0, x1, ys = width, 0, []
    for y, r in enumerate(rows):
        alpha = bytes(r[3::4]).translate(_VISIBLE)
        left = len(alpha) - len(alpha.lstrip(_NUL))
        if left == len(alpha):
            continue
        ys.append(y)
        x0 = min(x0, left)
        x1 = max(x1, len(alpha.rstrip(_NUL)))
    if not ys:
        return None
    return x0, ys[0], x1, ys[-1] + 1


def check_sprite(width: int, height: int, channels: int, rows: Sequence[bytes]) -> list[str]:
    """sprite 한 장의 규칙 위반 목록(크기·RGBA·가운데·머리 위 여백·발끝 기준선)."""
    if (width, height) != SPRITE_SIZE:
        return [f"크기 {width}x{height} ≠ {SPRITE_SIZE[0]}x{SPRITE_SIZE[1]}"]
    if channels != 4:
        return ["투명 채널 없음(RGBA로 저장)"]
    box = visible_box(width, rows)
    if box is None:
        return ["보이는 픽셀 없음"]
    y0 = box[1]
    problems = _center_problem(box, width)
    if y0 < TOP_MARGIN_MIN:
        problems.append(f"머리 위 여백 {y0}px < {TOP_MARGIN_MIN}px(머리가 잘릴 수 있음)")
    return problems + _foot_problem(box, height)


def _center_problem(box: tuple[int, int, int, int], width: int) -> list[str]:
    """보이는 픽셀 경계 상자의 가로 가운데가 width/2 ± CENTER_TOLERANCE 밖이면 위반 1건. sprite·마스코트 공통."""
    center = (box[0] + box[2]) / 2
    if abs(center - width / 2) > CENTER_TOLERANCE:
        return [f"가로 가운데 {center:g}px ≠ {width // 2}±{CENTER_TOLERANCE}px"]
    return []


def _foot_problem(box: tuple[int, int, int, int], height: int) -> list[str]:
    """아래 여백(가장 아래 보이는 픽셀 아래 투명 행 수)이 BOTTOM_MARGIN 밖이면 위반 1건. sprite·마스코트 공통.
    발끝 기준선은 그 범위의 가운데(sprite y=1248, 마스코트 y=480)."""
    bottom = height - box[3]
    if not BOTTOM_MARGIN[0] <= bottom <= BOTTOM_MARGIN[1]:
        foot = height - sum(BOTTOM_MARGIN) // 2
        return [f"발끝 아래 여백 {bottom}px ≠ {BOTTOM_MARGIN[0]}~{BOTTOM_MARGIN[1]}px(발끝 기준선 y={foot})"]
    return []


def _same_px(a: bytes, b: bytes, i: int) -> bool:
    """RGBA 픽셀 i(바이트 위치)가 같은가. 둘 다 완전 투명이면 RGB와 무관하게 같다."""
    if a[i + 3] == 0 and b[i + 3] == 0:
        return True
    return a[i:i + 4] == b[i:i + 4]


def check_expression(base: Sequence[bytes], variant: Sequence[bytes], box: tuple[int, int, int, int],
                     width: int = SPRITE_SIZE[0]) -> list[str]:
    """표정 변형 sprite가 기본 sprite와 얼굴 영역 `box`(그 인물의 `face_box`) 밖에서 정확히 같고,
    얼굴 영역은 달라졌는지 본다(둘 다 RGBA)."""
    if len(base) != len(variant) or any(len(a) != len(b) for a, b in zip(base, variant)):
        return ["기본 sprite와 크기·채널이 다름"]
    fx0, fy0, fx1, fy1 = box
    outside = inside = 0
    for y, (a, b) in enumerate(zip(base, variant)):
        a, b = bytes(a), bytes(b)
        if a == b:
            continue
        in_rows = fy0 <= y < fy1
        for x in range(width):
            if _same_px(a, b, x * 4):
                continue
            if in_rows and fx0 <= x < fx1:
                inside += 1
            else:
                outside += 1
    problems = []
    if outside:
        problems.append(f"얼굴 영역 밖 {outside}픽셀이 기본 sprite와 다름. 편집한 얼굴(x {fx0}~{fx1 - 1}, "
                        f"y {fy0}~{fy1 - 1})만 기본 sprite에 붙여 넣는다")
    if not inside:
        problems.append("얼굴 영역이 기본 sprite와 같음(표정 변화 없음)")
    return problems


def check_mascot(width: int, height: int, channels: int, rows: Sequence[bytes]) -> list[str]:
    """마스코트 한 장의 규칙 위반 목록(크기·RGBA·보이는 픽셀·모서리 투명·가로 가운데·아래 여백)."""
    if (width, height) != MASCOT_SIZE:
        return [f"크기 {width}x{height} ≠ {MASCOT_SIZE[0]}x{MASCOT_SIZE[1]}"]
    if channels != 4:
        return ["투명 채널 없음(RGBA로 저장)"]
    box = visible_box(width, rows)
    if box is None:
        return ["보이는 픽셀 없음"]
    placement = _center_problem(box, width) + _foot_problem(box, height)
    corners = [rows[y][x * 4 + 3] for y in (0, height - 1) for x in (0, width - 1)]
    if any(a >= VISIBLE_ALPHA for a in corners):
        return ["모서리가 투명하지 않음(바탕 투명)"]
    clear = sum(bytes(r[3::4]).translate(_VISIBLE).count(0) for r in rows)
    if clear < MASCOT_CLEAR_MIN * width * height:
        return [f"투명 픽셀 {clear / (width * height):.0%} < {MASCOT_CLEAR_MIN:.0%}(바탕 투명)"]
    return placement


def check_background(width: int, height: int, channels: int, rows: Sequence[bytes]) -> list[str]:
    """장면 배경 한 장의 규칙 위반 목록(크기·완전 불투명)."""
    if (width, height) != BACKGROUND_SIZE:
        return [f"크기 {width}x{height} ≠ {BACKGROUND_SIZE[0]}x{BACKGROUND_SIZE[1]}"]
    if channels == 4:
        holes = sum(1 for r in rows if bytes(r[3::4]).strip(_FF))
        if holes:
            return [f"투명·반투명 픽셀이 있는 행 {holes}개(배경은 완전 불투명)"]
    return []


# ---------------------------------------------------------------- 배치·합성
def _weights(src: int, dst: int) -> list[list[tuple[int, float]]]:
    """면적 평균 축소 가중치: 출력 칸마다 (입력 인덱스, 가중치) 목록. 가중치 합은 1."""
    scale = src / dst
    out = []
    for i in range(dst):
        a, b = i * scale, (i + 1) * scale
        taps = []
        for j in range(int(a), min(src, int(b) + 1)):
            w = min(b, j + 1) - max(a, j)
            if w > 1e-9:
                taps.append((j, w / scale))
        out.append(taps)
    return out


def resize_rgba(rows: Sequence[bytes], width: int, size: tuple[int, int]) -> list[bytearray]:
    """RGBA 행을 면적 평균(부드러운 축소)으로 `size`로 줄인다. 알파를 곱한 값으로 평균해 가장자리 색 번짐을 막는다."""
    nw, nh = size
    wx, wy = _weights(width, nw), _weights(len(rows), nh)
    horiz: list[list[float]] = []
    for r in rows:
        line = [0.0] * (nw * 4)
        for x, taps in enumerate(wx):
            pr = pg = pb = pa = 0.0
            for j, w in taps:
                a = r[j * 4 + 3] * w
                pr += r[j * 4] * a
                pg += r[j * 4 + 1] * a
                pb += r[j * 4 + 2] * a
                pa += a
            line[x * 4:x * 4 + 4] = (pr, pg, pb, pa)
        horiz.append(line)
    out: list[bytearray] = []
    for taps in wy:
        acc = [0.0] * (nw * 4)
        for j, w in taps:
            src = horiz[j]
            for k in range(nw * 4):
                acc[k] += src[k] * w
        row = bytearray(nw * 4)
        for x in range(nw):
            pa = acc[x * 4 + 3]
            if pa > 0:
                row[x * 4:x * 4 + 3] = bytes(min(255, round(acc[x * 4 + c] / pa)) for c in range(3))
                row[x * 4 + 3] = min(255, round(pa))
        out.append(row)
    return out


def place(mode: Mode, stage: tuple[int, int] = STAGE) -> tuple[int, int, int, int]:
    """무대 안 sprite 사각형 (x0, y0, x1, y1). 가로 가운데."""
    w, h = mode.size
    x0 = (stage[0] - w) // 2
    return x0, mode.top, x0 + w, mode.top + h


def dialogue_box(stage: tuple[int, int] = STAGE) -> tuple[int, int, int, int]:
    """대사창 사각형 (x0, y0, x1, y1): 무대 아래 180px 전체 폭."""
    return 0, stage[1] - DIALOGUE_HEIGHT, stage[0], stage[1]


DIALOGUE_FILL = bytes((0x1F, 0x24, 0x33))  # 깊은 밤(검수용 사각형, 실제 대사창은 HTML)
DIALOGUE_EDGE = bytes((0xE8, 0xDF, 0xC8))  # 한지 테두리


def _blend(out: list[bytearray], small: Sequence[bytes], x0: int, y0: int, w: int) -> None:
    for y, src in enumerate(small):
        dst = out[y0 + y]
        for x in range(w):
            a = src[x * 4 + 3]
            if a == 0:
                continue
            tx = (x0 + x) * 3
            dst[tx:tx + 3] = bytes((src[x * 4 + c] * a + dst[tx + c] * (255 - a) + 127) // 255 for c in range(3))


def compose(background: Sequence[bytes], bg_channels: int, sprite: Optional[Sequence[bytes]] = None,
            mode: Mode = NORMAL, sprite_width: int = SPRITE_SIZE[0],
            mascot: Optional[Sequence[bytes]] = None) -> list[bytearray]:
    """검수용 합성(RGB 행): 무대 배경 + sprite(모드대로 잘라 축소·배치, 알파 합성) + 마스코트(왼쪽 자리) +
    대사창 사각형(모드가 보일 때)."""
    if bg_channels == 4:
        out = [bytearray(b"".join(bytes(r[i:i + 3]) for i in range(0, len(r), 4))) for r in background]
    else:
        out = [bytearray(r) for r in background]
    if sprite is not None:
        y_from, y_to = mode.rows
        small = resize_rgba(sprite[y_from:y_to], sprite_width, mode.size)
        x0, y0, _, _ = place(mode)
        _blend(out, small, x0, y0, mode.size[0])
    if mascot is not None:
        mx, my, msize = MASCOT
        _blend(out, resize_rgba(mascot, MASCOT_SIZE[0], msize), mx, my, msize[0])
    if mode.dialogue or sprite is None:
        x0, y0, x1, y1 = dialogue_box()
        for y in range(y0, y1):
            edge = y < y0 + 4 or y >= y1 - 4
            row = out[y]
            row[x0 * 3:x1 * 3] = (DIALOGUE_EDGE if edge else DIALOGUE_FILL) * (x1 - x0)
            if not edge:
                row[x0 * 3:(x0 + 4) * 3] = DIALOGUE_EDGE * 4
                row[(x1 - 4) * 3:x1 * 3] = DIALOGUE_EDGE * 4
    return out
