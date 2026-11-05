"""임시 이미지 그리기(도메인). sprite·초상은 회색 실루엣 + 기준선 + 이름표(`portrait_art`), 장면 배경은 그라데이션 +
이름표(`background_art`), 마스코트는 동물 실루엣 + 이름표(`mascot_art`). 모두 크기·기준선·배치 확인용이며 최종 그림이 아니다. 나머지는 단색 배경 + 도형(글자 없음).
같은 (kind, id, 이름, 크기)면 같은 픽셀.

초상 외 색과 도형은 kind마다 고정이고, id에서 나온 해시로 보조색만 고른다. 해시는 게임 규칙과 무관하므로
임시 이미지가 새 게임 정보(예: 씐 자 암시)를 주지 않는다.
"""
from __future__ import annotations

import hashlib

from tools.assets import background_art, mascot_art, portrait_art

RGB = tuple[int, int, int]

BACKGROUND: dict[str, RGB] = {
    "portrait": (0x2E, 0x34, 0x4A),
    "background": (0x2E, 0x34, 0x4A),
    "place": (0x3B, 0x4A, 0x3F),
    "ui": (0x4A, 0x3B, 0x3B),
    "map": (0x5A, 0x55, 0x45),
    "ad": (0x33, 0x33, 0x33),
}
FOREGROUND: RGB = (0xC8, 0xC2, 0xB4)
ACCENTS: tuple[RGB, ...] = (
    (0xB0, 0x4A, 0x3A), (0x3A, 0x6E, 0xB0), (0xC9, 0xA2, 0x3A),
    (0x4E, 0x9A, 0x5C), (0x8A, 0x5A, 0xA8), (0x3A, 0xA0, 0xA0),
)


class Canvas:
    """RGB 픽셀 버퍼. 행마다 bytearray(가로 × 3)."""

    def __init__(self, width: int, height: int, color: RGB):
        if width <= 0 or height <= 0:
            raise ValueError(f"크기가 0 이하: {width}x{height}")
        self.width, self.height = width, height
        row = bytes(color) * width
        self.rows = [bytearray(row) for _ in range(height)]

    def _span(self, y: int, x0: int, x1: int, color: RGB) -> None:
        x0, x1 = max(0, x0), min(self.width, x1)
        if 0 <= y < self.height and x0 < x1:
            self.rows[y][x0 * 3:x1 * 3] = bytes(color) * (x1 - x0)

    def rect(self, x0: int, y0: int, x1: int, y1: int, color: RGB) -> None:
        """[x0, x1) × [y0, y1) 사각형을 채운다."""
        for y in range(max(0, y0), min(self.height, y1)):
            self._span(y, x0, x1, color)

    def ellipse(self, cx: float, cy: float, rx: float, ry: float, color: RGB) -> None:
        """중심 (cx, cy), 반지름 (rx, ry) 타원을 채운다."""
        for y in range(max(0, int(cy - ry)), min(self.height, int(cy + ry) + 1)):
            t = (y + 0.5 - cy) / ry
            if abs(t) > 1:
                continue
            half = rx * (1 - t * t) ** 0.5
            self._span(y, int(round(cx - half)), int(round(cx + half)), color)


def accent_for(kind: str, cid: str) -> RGB:
    """id에서 정해지는 보조색(규칙과 무관한 해시)."""
    digest = hashlib.sha256(f"{kind}:{cid}".encode("utf-8")).digest()
    return ACCENTS[digest[0] % len(ACCENTS)]


def render_rows(kind: str, cid: str, width: int, height: int, name: str | None = None,
                expression: str | None = None) -> tuple[int, list[bytearray]]:
    """임시 이미지 1장 → (채널 수, 행 목록). sprite·마스코트는 RGBA(4, 바탕 투명), 나머지는 RGB(3).
    `cid`는 표정 접미사를 뗀 인물 id, `name`은 sprite·초상 이름표에 쓰는 공개 표시 이름(없으면 id),
    `expression`은 표정 변형의 표정 이름(기본이면 None)."""
    if kind == "sprite":
        return 4, portrait_art.render_sprite(cid, width, height, name, expression)
    if kind == "mascot":
        return 4, mascot_art.render(cid, width, height)
    return 3, render(kind, cid, width, height, name, expression).rows


def render(kind: str, cid: str, width: int, height: int, name: str | None = None,
           expression: str | None = None) -> Canvas:
    """불투명(RGB) 임시 이미지 1장을 그린다. sprite는 `render_rows`로."""
    if kind not in BACKGROUND:
        raise ValueError(f"알 수 없는 kind: {kind!r}")
    c = Canvas(width, height, BACKGROUND[kind])
    acc = accent_for(kind, cid)
    w, h = width, height
    if kind == "portrait":                 # sprite 위 512×512 크롭 + 먹남 알파 합성
        c.rows = portrait_art.render(cid, w, h, name, expression)
    elif kind == "background":             # 그라데이션 + 이름표
        c.rows = background_art.render(cid, w, h)
    elif kind == "place":                  # 둥근 판 + 보조색 사각
        c.ellipse(w / 2, h / 2, w * 0.42, h * 0.42, FOREGROUND)
        c.rect(int(w * 0.34), int(h * 0.34), int(w * 0.66), int(h * 0.66), acc)
    elif kind == "ui":                     # 원 + 보조색 작은 원
        c.ellipse(w / 2, h / 2, w * 0.40, h * 0.40, acc)
        c.ellipse(w / 2, h / 2, w * 0.18, h * 0.18, FOREGROUND)
    elif kind == "map":                    # 바탕 + 띠 2개(장소 위치를 암시하지 않는 고정 무늬)
        c.rect(0, int(h * 0.45), w, int(h * 0.55), FOREGROUND)
        c.rect(int(w * 0.45), 0, int(w * 0.55), h, acc)
    elif kind == "ad":                     # 테두리 + 왼쪽 원
        b = max(2, min(w, h) // 30)
        c.rect(0, 0, w, b, acc); c.rect(0, h - b, w, h, acc)
        c.rect(0, 0, b, h, acc); c.rect(w - b, 0, w, h, acc)
        r = min(w, h) * 0.32
        c.ellipse(r * 1.6, h / 2, r, r, FOREGROUND)
    return c
