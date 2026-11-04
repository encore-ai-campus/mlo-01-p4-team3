"""PNG 쓰기·머리 읽기(표준 라이브러리만). 8비트 RGB, 필터 0, zlib 9. 같은 입력이면 같은 바이트."""
from __future__ import annotations

import struct
import zlib
from typing import Sequence

SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_rgb(width: int, height: int, rows: Sequence[bytes]) -> bytes:
    """RGB 행(각 가로 × 3바이트) → PNG 바이트."""
    if len(rows) != height or any(len(r) != width * 3 for r in rows):
        raise ValueError("행 수나 행 길이가 크기와 맞지 않음")
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, 9)) + _chunk(b"IEND", b"")


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
