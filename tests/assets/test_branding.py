"""B-10-08 로고의 비율·투명 배경과 규격 위반 검출을 확인한다."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.assets import branding, png, sources
from tools.assets.make_placeholders import build_expected, inspect_files


class Branding(unittest.TestCase):
    """원화 재생성 없이 면적 평균 정규화와 공식 검사 연결을 검증한다."""

    def test_wide_logo_keeps_ratio_with_transparent_padding(self):
        """가로 2:1 그림을 늘이지 않고 정사각 투명 캔버스 가운데 둔다."""
        rows = [bytearray((30, 70, 100, 255) * 4) for _ in range(2)]
        width, height, channels, normalized = png.decode(branding.normalize_logo(4, 2, 4, rows))
        self.assertEqual((width, height, channels), (512, 512, 4))
        visible = [y for y, row in enumerate(normalized) if any(row[3::4])]
        self.assertEqual((min(visible), max(visible)), (128, 383))
        self.assertEqual(branding.check_logo(width, height, channels, normalized), [])

    def test_transparent_or_rgb_logo_is_rejected(self):
        """빈 파일이나 투명 채널 없는 로고를 정식 그림으로 받지 않는다."""
        with self.assertRaises(ValueError):
            branding.normalize_logo(4, 2, 3, [bytes(12)] * 2)
        with self.assertRaises(ValueError):
            branding.normalize_logo(4, 2, 4, [bytes(16)] * 2)

    def test_asset_inspection_rejects_rgb_branding(self):
        """기존 check_assets 경로도 branding의 채널 위반을 검사한다."""
        spec = sources.load_spec()
        target = next(e for e in build_expected(spec, sources.BUNDLE_PATH, sources.CAMPAIGNS_PATH)
                      if e.kind == 'branding')
        with TemporaryDirectory() as directory:
            path = Path(directory) / target.path
            path.parent.mkdir(parents=True)
            path.write_bytes(png.encode_rgb(512, 512, [bytes((20, 30, 40)) * 512] * 512))
            _, failures = inspect_files(Path(directory), [target], spec)
            self.assertTrue(any('RGBA' in failure for failure in failures), failures)
