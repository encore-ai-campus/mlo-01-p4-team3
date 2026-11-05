"""PNG 쓰기·읽기(표준 라이브러리만, 인프라). 쓰기는 8비트 RGB/RGBA, zlib 9(같은 입력이면 같은 바이트).
필터는 기본 0(임시 그림), `optimize=True`면 행마다 0·1·2·4 중 절댓값 합이 가장 작은 것(실제 sprite에서 파생한 초상이
용량 상한 512,000바이트를 지키도록).
읽기(`decode`)는 8비트 RGB/RGBA, 인터레이스 없음, 필터 0~4를 지원한다(생성 도구가 만든 실제 sprite를 크롭할 때 쓴다)."""
from __future__ import annotations

import struct
import zlib
from typing import Sequence

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _filtered(line: bytes, prev: bytes, ch: int) -> bytes:
    """한 행에 필터 0·1(Sub)·2(Up)·4(Paeth)를 적용해 부호 있는 절댓값 합이 가장 작은 것(필터 바이트 포함)."""
    n = len(line)
    sub = bytes((line[i] - (line[i - ch] if i >= ch else 0)) & 0xFF for i in range(n))
    up = bytes((line[i] - prev[i]) & 0xFF for i in range(n))
    paeth = bytes((line[i] - _paeth(line[i - ch] if i >= ch else 0, prev[i], prev[i - ch] if i >= ch else 0)) & 0xFF
                  for i in range(n))
    best = min(((0, line), (1, sub), (2, up), (4, paeth)),
               key=lambda c: sum(v if v < 128 else 256 - v for v in c[1]))
    return bytes((best[0],)) + best[1]


def _encode(width: int, height: int, rows: Sequence[bytes], channels: int, color_type: int,
            optimize: bool = False) -> bytes:
    if len(rows) != height or any(len(r) != width * channels for r in rows):
        raise ValueError("행 수나 행 길이가 크기와 맞지 않음")
    if optimize:
        prev, parts = bytes(width * channels), []
        for r in rows:
            r = bytes(r)
            parts.append(_filtered(r, prev, channels))
            prev = r
        raw = b"".join(parts)
    else:
        raw = b"".join(b"\x00" + bytes(r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


def encode_rgb(width: int, height: int, rows: Sequence[bytes], optimize: bool = False) -> bytes:
    """RGB 행(각 가로 × 3바이트) → PNG 바이트. optimize면 행마다 필터를 골라 작게(느림)."""
    return _encode(width, height, rows, 3, 2, optimize)


def encode_rgba(width: int, height: int, rows: Sequence[bytes], optimize: bool = False) -> bytes:
    """RGBA 행(각 가로 × 4바이트) → PNG 바이트(투명 sprite). optimize면 행마다 필터를 골라 작게(느림)."""
    return _encode(width, height, rows, 4, 6, optimize)


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def decode(data: bytes) -> tuple[int, int, int, list[bytes]]:
    """PNG → (가로, 세로, 채널 수 3|4, 행 목록). 8비트 RGB/RGBA·인터레이스 없음만. 그 밖이면 ValueError."""
    width, height = read_size(data)
    pos, idat, ihdr = 8, [], b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            ihdr = body
        elif tag == b"IDAT":
            idat.append(body)
        pos += 12 + length
    _, _, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8 or color not in (2, 6) or interlace != 0:
        raise ValueError(f"지원하지 않는 PNG 형식(비트 {depth}, 색 {color}, 인터레이스 {interlace}). 8비트 RGB/RGBA로 저장")
    ch = 3 if color == 2 else 4
    stride = width * ch
    raw = zlib.decompress(b"".join(idat))
    rows: list[bytes] = []
    prev = bytearray(stride)
    for y in range(height):
        off = y * (stride + 1)
        ftype, line = raw[off], bytearray(raw[off + 1:off + 1 + stride])
        if ftype == 0:
            pass
        elif ftype == 2:
            line = bytearray((line[i] + prev[i]) & 0xFF for i in range(stride))
        elif ftype in (1, 3, 4):
            for i in range(stride):
                a = line[i - ch] if i >= ch else 0
                b = prev[i]
                if ftype == 1:
                    line[i] = (line[i] + a) & 0xFF
                elif ftype == 3:
                    line[i] = (line[i] + (a + b) // 2) & 0xFF
                else:
                    c = prev[i - ch] if i >= ch else 0
                    line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError(f"알 수 없는 필터 {ftype}")
        rows.append(bytes(line))
        prev = line
    return width, height, ch, rows


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}   # PNG 색 형식 → 채널 수


def read_size(data: bytes) -> tuple[int, int]:
    """PNG 바이트를 검사하고 (가로, 세로)를 돌려준다.

    청크 구조·CRC, IHDR이 처음·IEND가 끝, IDAT 존재, 압축 해제한 이미지 데이터 길이를 본다.
    하나라도 어긋나면 ValueError(잘리거나 깨진 파일).
    """
    if data[:8] != SIGNATURE:
        raise ValueError("PNG 서명이 아님")
    pos, chunks, idat = 8, [], []
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("청크가 잘림")
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length or pos + 12 + length > len(data):
            raise ValueError(f"{tag!r} 청크가 잘림")
        (crc,) = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
        if zlib.crc32(tag + body) & 0xFFFFFFFF != crc:
            raise ValueError(f"{tag!r} 청크 CRC 불일치")
        chunks.append((tag, body))
        if tag == b"IDAT":
            idat.append(body)
        pos += 12 + length
        if tag == b"IEND":
            break
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ValueError("IHDR이 처음에 없음")
    if chunks[-1][0] != b"IEND" or pos != len(data):
        raise ValueError("IEND로 끝나지 않음")
    if not idat:
        raise ValueError("IDAT 없음")
    width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", chunks[0][1])
    if width == 0 or height == 0 or color not in _CHANNELS:
        raise ValueError("IHDR 값이 잘못됨")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise ValueError(f"이미지 데이터 압축 해제 실패: {exc}") from None
    if interlace not in (0, 1):
        raise ValueError("IHDR 인터레이스 값이 잘못됨")
    if len(raw) != expected_data_length(width, height, _CHANNELS[color] * depth, interlace == 1):
        raise ValueError("이미지 데이터 길이가 크기와 맞지 않음")
    return width, height


_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def expected_data_length(width: int, height: int, bits_per_pixel: int, interlaced: bool) -> int:
    """압축 해제한 이미지 데이터의 바이트 수(행마다 필터 1바이트 포함). Adam7이면 7단계 합."""
    def scan(w: int, h: int) -> int:
        return 0 if w == 0 or h == 0 else h * (1 + (w * bits_per_pixel + 7) // 8)
    if not interlaced:
        return scan(width, height)
    return sum(scan((width - x0 + dx - 1) // dx if width > x0 else 0,
                    (height - y0 + dy - 1) // dy if height > y0 else 0)
               for x0, y0, dx, dy in _ADAM7)
