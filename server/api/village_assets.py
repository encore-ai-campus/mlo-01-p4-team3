"""고정 안내 지도·공개 주민 풀의 manifest 이미지와 출처를 화면에 연결한다."""
import json
from pathlib import Path
import struct

from django.conf import settings

from .viewmodels import PLACE_KO


def village_assets():
    """판 상태를 받지 않고 공개 이름·에셋 정보만 반환한다. 역할·이동 정보는 사용하지 않는다."""
    root = Path(settings.BASE_DIR)
    items = json.loads(Path(settings.ASSET_MANIFEST).read_text(encoding='utf-8'))
    index = {f"{row['kind']}:{row['id']}": row for row in items}
    catalog = root / 'content/bundle_v1.json'
    pool = root / 'fixtures/content_min/bundle.json'  # 에셋 규격의 주민 풀 ID 원본
    names = {}
    for source in (pool, catalog):
        if source.exists():
            names.update({c['npc_id']: c['name_ko'] for c in json.loads(
                source.read_text(encoding='utf-8'))['characters']})
    portraits = [dict(asset_id=f"portrait:{row['id']}", url=row['url'],
                      name=names.get(row['id'], {'mudang_f': '무당 · 여성', 'mudang_m': '무당 · 남성'}.get(row['id'], row['id'])),
                      placeholder=row.get('placeholder', False))
                 for row in items if row['kind'] == 'portrait' and '__' not in row['id']]
    map_item = index.get('map:village')
    village = None
    if map_item:
        path = (root / map_item['path']).resolve()
        if not path.is_relative_to((root / 'content/images').resolve()):
            raise ValueError('invalid_map_path')
        with path.open('rb') as image:
            header = image.read(24)
        if header[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('invalid_map_png')
        width, height = struct.unpack('!II', header[16:24])
        if not width or not height:
            raise ValueError('invalid_map_size')
        places = []
        for place, box in map_item.get('regions', {}).items():
            icon = index.get(f'place:{place}')
            if place not in PLACE_KO or icon is None:
                continue
            x, y, w, h = box
            places.append(dict(name=PLACE_KO[place], url=icon['url'],
                left=f'{100 * x / width:.4f}', top=f'{100 * y / height:.4f}',
                width=f'{100 * w / width:.4f}', height=f'{100 * h / height:.4f}'))
        village = dict(url=map_item['url'], width=width, height=height, places=places,
                       placeholder=map_item.get('placeholder', False))
    return dict(village=village, portraits=portraits)


def image_source():
    """SOURCES의 첫 공개 출처 줄만 읽어 프롬프트·생성 기록 전체를 노출하지 않는다."""
    source_path = Path(settings.BASE_DIR) / 'content/images/SOURCES.md'
    source = next((line.strip() for line in source_path.read_text(encoding='utf-8').splitlines()
                   if line.strip()), '') if source_path.exists() else ''
    return source
