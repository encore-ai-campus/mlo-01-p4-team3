"""임시 에셋 이름표용 글자 비트맵 만들기(개발 도구, 저장소 관리자만 가끔 실행).

    python -m tools.assets.build_glyphs                       # 기본 글꼴(맑은 고딕)로 glyph_data.py를 다시 쓴다
    python -m tools.assets.build_glyphs --font "<Windows 글꼴 이름>"

- 필요한 글자: 고정 문구(`portrait_art.LABEL_PREFIX`, `portrait_art.HERO_LABELS`, `background_art.LABELS`, `mascot_art.LABELS`)
  + 규격표 8절 표정 이름 + 콘텐츠 묶음 인물 이름
  (`name_ko`) + 영문 소문자·숫자·`_-()`(이름 글자가 없을 때 ID로 대신 쓴다).
- 결과 `tools/assets/glyph_data.py`는 저장소에 커밋한다. 생성기(`make_placeholders`)는 그 데이터만 쓰므로 팀원 노트북의 글꼴과
  상관없이 같은 바이트가 나온다. 이 스크립트는 Windows GDI(ctypes, 표준 라이브러리)로 글자를 그리므로 Windows에서만 돈다.
- 인물 이름을 새로 추가하면(예: E-08-02 확장 주민) 이 스크립트를 다시 돌리고 glyph_data.py를 함께 커밋한다. 다시 돌리지 않아도
  이름 대신 ID가 찍힐 뿐 실패하지 않는다.

계층: 표현(CLI) + 인프라(GDI 글꼴 렌더링·파일 쓰기). 그리기 규칙은 `glyphs.py`(도메인).
"""
from __future__ import annotations

import argparse
import string
import sys
from pathlib import Path

from tools.assets import background_art, mascot_art, portrait_art, sources

DEFAULT_FONT = "Malgun Gothic"
SIZE = 32
OUT = Path(__file__).with_name("glyph_data.py")
ASCII = string.ascii_lowercase + string.digits + "_-()"


def needed_chars(bundle_path: Path = sources.BUNDLE_PATH) -> str:
    """비트맵으로 만들 글자(정렬, 공백 제외)."""
    texts = [portrait_art.LABEL_PREFIX, *portrait_art.HERO_LABELS.values(), *background_art.LABELS.values(),
             *mascot_art.LABELS.values(), *(label for _, label in sources.load_spec().expressions),
             *sources.load_npc_labels(bundle_path).values(), ASCII]
    return "".join(sorted({ch for t in texts for ch in t if not ch.isspace()}))


def render(face: str, chars: str, size: int = SIZE) -> tuple[int, dict[str, tuple[int, tuple[int, ...]]]]:
    """Windows GDI(ctypes, 표준 라이브러리)로 글자마다 (폭, 행 비트마스크)를 만든다. 최상위 비트가 왼쪽 픽셀이다."""
    import ctypes
    from ctypes import wintypes

    gdi = ctypes.windll.gdi32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class TEXTMETRICW(ctypes.Structure):
        _fields_ = [("tmHeight", wintypes.LONG), ("tmAscent", wintypes.LONG), ("tmDescent", wintypes.LONG),
                    ("rest", ctypes.c_byte * 48)]

    gdi.CreateFontW.restype = wintypes.HANDLE
    gdi.CreateCompatibleDC.restype = wintypes.HANDLE
    gdi.CreateDIBSection.restype = wintypes.HANDLE
    gdi.SelectObject.restype = wintypes.HANDLE
    gdi.SelectObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    for fn in ("SetTextColor", "SetBkColor", "SetBkMode", "TextOutW", "GetTextExtentPoint32W", "GetTextMetricsW",
               "DeleteObject", "DeleteDC", "GdiFlush"):
        getattr(gdi, fn).restype = ctypes.c_long
    gdi.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi.DeleteDC.argtypes = [wintypes.HANDLE]
    gdi.SetBkMode.argtypes = [wintypes.HANDLE, ctypes.c_int]
    gdi.SetBkColor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    gdi.SetTextColor.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    gdi.GetTextMetricsW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    gdi.CreateDIBSection.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
                                     wintypes.HANDLE, wintypes.DWORD]
    gdi.TextOutW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
    gdi.GetTextExtentPoint32W.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_int,
                                          ctypes.POINTER(wintypes.SIZE)]
    font = gdi.CreateFontW(-size, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 4, 0, face)   # 4 = ANTIALIASED_QUALITY
    if not font:
        raise OSError(f"글꼴을 만들 수 없음: {face}")
    dc = gdi.CreateCompatibleDC(None)
    gdi.SelectObject(dc, font)
    tm = TEXTMETRICW()
    gdi.GetTextMetricsW(dc, ctypes.byref(tm))
    height = tm.tmHeight
    out: dict[str, tuple[int, tuple[int, ...]]] = {}
    try:
        for ch in chars:
            ext = wintypes.SIZE()
            gdi.GetTextExtentPoint32W(dc, ch, 1, ctypes.byref(ext))
            width = max(1, ext.cx)
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            dib = gdi.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            old = gdi.SelectObject(dc, dib)
            gdi.SetBkMode(dc, 2)                   # OPAQUE: 검은 바탕
            gdi.SetBkColor(dc, 0x000000)
            gdi.SetTextColor(dc, 0xFFFFFF)
            gdi.TextOutW(dc, 0, 0, ch, 1)
            gdi.GdiFlush()
            raw = ctypes.string_at(bits, width * height * 4)
            rows = []
            for y in range(height):
                v = 0
                for x in range(width):
                    v = (v << 1) | (1 if raw[(y * width + x) * 4 + 1] >= 128 else 0)
                rows.append(v)
            out[ch] = (width, tuple(rows))
            gdi.SelectObject(dc, old)
            gdi.DeleteObject(dib)
    finally:
        gdi.DeleteDC(dc)
        gdi.DeleteObject(font)
    return height, out


def write(height: int, glyphs: dict[str, tuple[int, tuple[int, ...]]], font_name: str, path: Path = OUT) -> None:
    lines = [
        '"""임시 에셋 이름표 글자 비트맵(자동 생성: python -m tools.assets.build_glyphs). 손으로 고치지 않는다."""',
        f"FONT = {font_name!r}",
        f"HEIGHT = {height}",
        "GLYPHS: dict[str, tuple[int, tuple[int, ...]]] = {",
    ]
    for ch, (w, rows) in sorted(glyphs.items()):
        lines.append(f"    {ch!r}: ({w}, ({', '.join(hex(r) for r in rows)},)),")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="임시 에셋 이름표 글자 비트맵 생성")
    ap.add_argument("--font", default=DEFAULT_FONT, help="Windows 글꼴 이름(기본 Malgun Gothic)")
    args = ap.parse_args(argv)
    if sys.platform != "win32":
        print("Windows에서만 실행한다(GDI로 글자를 그린다)", file=sys.stderr)
        return 2
    try:
        height, glyphs = render(args.font, needed_chars())
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write(height, glyphs, args.font)
    print(f"{OUT} ({len(glyphs)}자, 높이 {height}px)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
