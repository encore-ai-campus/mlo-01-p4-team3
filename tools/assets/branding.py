"""B-10-08: 승인 시안의 로고를 비율·투명도를 유지해 공식 규격으로 등록한다."""
from dataclasses import replace
import argparse
import hashlib
from pathlib import Path

from tools.assets import png, sources, vn
from tools.assets.spec import BRANDING_IDS, sort_key


def check_logo(width, height, channels, rows):
    """512×512 RGBA 로고의 가시 픽셀과 투명 배경을 검사한다."""
    if (width, height, channels) != (512, 512, 4):
        return ['로고는 512×512 RGBA여야 함']
    alpha = b''.join(bytes(row[3::4]) for row in rows)
    if not any(alpha):
        return ['로고의 보이는 픽셀이 없음']
    if min(alpha) == 255:
        return ['로고의 투명 배경이 없음']
    return []


def normalize_logo(width, height, channels, rows):
    """같은 배율의 면적 평균 축소와 투명 여백으로 512×512 PNG를 만든다."""
    if channels != 4:
        raise ValueError('원본은 RGBA PNG여야 함')
    scale = min(512 / width, 512 / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    resized = vn.resize_rgba(rows, width, size)
    canvas = [bytearray(512 * 4) for _ in range(512)]
    x, y = (512 - size[0]) // 2, (512 - size[1]) // 2
    for index, row in enumerate(resized):
        canvas[y + index][x * 4:(x + size[0]) * 4] = row
    failures = check_logo(512, 512, 4, canvas)
    if failures:
        raise ValueError('; '.join(failures))
    return png.encode_rgba(512, 512, canvas, optimize=True)


def main():
    """시안 원본을 보존하고 정규화한 공식 로고·manifest만 쓴다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    args = parser.parse_args()
    spec = sources.load_spec(args.repo / 'docs/assets/spec.md')
    from tools.assets.make_placeholders import build_expected
    target = next(e for e in build_expected(spec, args.repo / 'fixtures/content_min/bundle.json',
                                           args.repo / 'fixtures/ads/campaigns.json')
                  if e.kind == 'branding' and e.id == BRANDING_IDS[0])
    logger = sources.JsonLogger(args.repo, component='branding')
    raw = args.source.read_bytes()
    logger.log('info', 'logo_normalize_start', source_file=args.source.name,
               source_sha256=hashlib.sha256(raw).hexdigest())
    data = normalize_logo(*png.decode(raw))
    if len(data) > spec.max_bytes_for('branding'):
        raise ValueError('정규화 로고가 용량 상한을 넘음')
    logger.log('info', 'logo_normalized', width=512, height=512, bytes=len(data))
    destination = args.repo / target.path
    if destination.exists() and destination.read_bytes() != data:
        raise FileExistsError('기존 로고를 다른 그림으로 덮어쓰지 않음')
    sources.write_file(args.repo, target.path, data)
    entries = [e for e in sources.load_manifest(args.repo, spec) if e.key != target.key]
    entries.append(replace(target, placeholder=False))
    sources.write_manifest(args.repo, spec, sorted(entries, key=sort_key))
    logger.log('info', 'logo_registered', content_id='branding:tiger_emblem',
               path=target.path, sha256=hashlib.sha256(data).hexdigest())


if __name__ == '__main__':
    main()
