"""비주얼 노벨 화면용 에셋 규칙(도메인): sprite·장면 배경 검사, 화면 배치 수치, 검수용 합성. 파일·OS를 건드리지 않는다.

화풍은 반실사 일러스트(11/5 PM 결정)라 색 수·반투명은 검사하지 않는다(사람 검수, 스타일 가이드 체크리스트).

- sprite(사람마다 전신 1장): 512×1280 RGBA, 8비트 알파. 보이는 픽셀(알파 ≥ 16, 잔여 노이즈 무시)의 경계 상자 가로 가운데가 256±16px,
  머리 위 여백 16px 이상, 발끝 아래 여백 16~48px(발끝 기준선 y=1248). 눈높이 y=200·허리선 y=640은 기준선이며
  그림에서 자동으로 찾을 수 없어 사람 검수 항목이다(spec.md 2절).
- 장면 배경: 1280×720, 완전 불투명(알파가 있으면 모두 255).
- 배치(B-10-07 기준, 무대 1280×720, 대사창 아래 180px):
  - 평소(`normal`): sprite 0~640행(머리~허리)을 부드럽게 0.8배 → 410×512, 가운데, 위 y=28·아랫변 y=540(대사창 윗변).
  - 전신 컷(`full`): sprite 전체를 0.5배 → 256×640, 가운데, 위 y=40. 컷 동안 대사창을 숨긴다.
  - 밤 화면에는 sprite가 없다. 밤 습격 결과 전신 컷은 아침 회의 화면에서 보인다.

검사 함수는 문제 목록(문자열)을 돌려준다. 빈 목록이면 통과.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

SPRITE_SIZE = (512, 1280)
BACKGROUND_SIZE = (1280, 720)
EYE_Y = 200                                # 눈높이 기준선(px, 사람 검수)
WAIST_Y = 640                              # 허리선(px). 평소 표시는 0~640행
FOOT_Y = 1248                              # 발끝 기준선(px)
CENTER_TOLERANCE = 16                      # 경계 상자 가운데 허용 오차(px)
TOP_MARGIN_MIN = 16                        # 머리 위 최소 여백(px)
BOTTOM_MARGIN = (16, 48)                   # 발끝 아래 여백(px). 기준선 1248 ± 16

STAGE = (1280, 720)                        # 무대(배경) 크기
DIALOGUE_HEIGHT = 180                      # 대사창 높이(무대 아래 1/4을 덮음)
NIGHT_SPRITES = 0                          # 밤 장면 sprite 수(밤 실행자 노출 금지)


@dataclass(frozen=True)
class Mode:
    """sprite 표시 방식: 잘라 쓸 행 범위, 축소 후 크기, 무대 안 위쪽 y, 대사창 표시 여부."""
    name: str
    rows: tuple[int, int]
    size: tuple[int, int]
    top: int
    dialogue: bool


NORMAL = Mode("normal", (0, WAIST_Y), (410, 512), 28, True)       # 아랫변 28 + 512 = 540 = 대사창 윗변
FULL = Mode("full", (0, SPRITE_SIZE[1]), (256, 640), 40, False)   # 전신 컷(첫 등장·추방/처형 결과·엔딩·밤 습격 결과)
MODES = {m.name: m for m in (NORMAL, FULL)}

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
    problems: list[str] = []
    x0, y0, x1, y1 = box
    center = (x0 + x1) / 2
    if abs(center - width / 2) > CENTER_TOLERANCE:
        problems.append(f"가로 가운데 {center:g}px ≠ {width // 2}±{CENTER_TOLERANCE}px")
    if y0 < TOP_MARGIN_MIN:
        problems.append(f"머리 위 여백 {y0}px < {TOP_MARGIN_MIN}px(머리가 잘릴 수 있음)")
    bottom = height - y1
    if not BOTTOM_MARGIN[0] <= bottom <= BOTTOM_MARGIN[1]:
        problems.append(f"발끝 아래 여백 {bottom}px ≠ {BOTTOM_MARGIN[0]}~{BOTTOM_MARGIN[1]}px(발끝 기준선 y={FOOT_Y})")
    return problems


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


def compose(background: Sequence[bytes], bg_channels: int, sprite: Optional[Sequence[bytes]] = None,
            mode: Mode = NORMAL, sprite_width: int = SPRITE_SIZE[0]) -> list[bytearray]:
    """검수용 합성(RGB 행): 무대 배경 + sprite(모드대로 잘라 축소·배치, 알파 합성) + 대사창 사각형(모드가 보일 때)."""
    if bg_channels == 4:
        out = [bytearray(b"".join(bytes(r[i:i + 3]) for i in range(0, len(r), 4))) for r in background]
    else:
        out = [bytearray(r) for r in background]
    if sprite is not None:
        y_from, y_to = mode.rows
        small = resize_rgba(sprite[y_from:y_to], sprite_width, mode.size)
        x0, y0, _, _ = place(mode)
        for y, src in enumerate(small):
            dst = out[y0 + y]
            for x in range(mode.size[0]):
                a = src[x * 4 + 3]
                if a == 0:
                    continue
                tx = (x0 + x) * 3
                dst[tx:tx + 3] = bytes((src[x * 4 + c] * a + dst[tx + c] * (255 - a) + 127) // 255
                                       for c in range(3))
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
