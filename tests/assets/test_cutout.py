"""A-08-04: 실제 바탕 제거·가장자리 역합성·비율 유지·CLI 실패 시 원본 보존."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tools.assets import cutout, png, vn


class CutoutTest(unittest.TestCase):
    def test_noisy_matte_does_not_leave_key_color_on_partial_edge(self):
        rows = [bytes((0, 255, 0, 5, 160, 5, 40, 40, 40))]
        out = cutout.remove_matte(3, 1, 3, rows, (0, 255, 0))[0]
        self.assertTrue(0 < out[7] < 255)
        self.assertLessEqual(out[5], max(out[4], out[6]))

    def test_colored_edge_recovers_foreground_not_premultiplied_color(self):
        rows = [bytes((0, 255, 0, 100, 153, 10, 200, 50, 20))]
        out = cutout.remove_matte(3, 1, 3, rows, (0, 255, 0))[0]
        for got, want in zip(out[4:8], (200, 50, 20, 128)):
            self.assertLessEqual(abs(got - want), 1)

    def test_invalid_png_rows_and_background_are_rejected(self):
        for args in ((0, 1, 3, [b''], (0, 255, 0)),
                     (1, 1, 3, [bytes(2)], (0, 255, 0)),
                     (1, 1, 3, [bytes(3)], (0, 256, 0))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                cutout.remove_matte(*args)

    def test_existing_output_is_preserved_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp)/'missing.png', Path(temp)/'already.png'
            target.write_bytes(b'keep existing file')
            with contextlib.redirect_stderr(io.StringIO()):
                result = cutout.main([str(source), str(target), '--id', 'jumo', '--head-top', '0'])
            self.assertEqual(result, 2)
            self.assertEqual(target.read_bytes(), b'keep existing file')

    def test_dark_hair_green_spill_is_not_mistaken_for_opaque_black(self):
        # (10,0,10) 전경 80% + 초록 20% = (8,51,8).
        rows = [bytes((10, 0, 10, 8, 51, 8, 10, 0, 10))]
        out = cutout.remove_matte(3, 1, 3, rows, (0, 255, 0))[0]
        self.assertLessEqual(abs(out[7] - 204), 1)
        self.assertEqual(tuple(out[4:7]), (10, 0, 10))

    def test_thin_detached_hair_can_use_nearby_solid_color(self):
        rows = [bytes((20, 148, 20)) + bytes((0, 255, 0)) * 10 + bytes((40, 40, 40)) * 2]
        out = cutout.remove_matte(13, 1, 3, rows, (0, 255, 0))[0]
        self.assertLessEqual(abs(out[3] - 128), 1)
        self.assertTrue(all(abs(v - 40) <= 1 for v in out[:3]))

    def test_small_generated_background_color_noise_is_transparent(self):
        # 실제 생성 원본 모서리: 순수 00ff00 대신 (17,241,14).
        rows = [bytes((17, 241, 14, 0, 255, 0, 40, 40, 40, 40, 40, 40))]
        out = cutout.remove_matte(4, 1, 3, rows, (0, 255, 0))[0]
        self.assertEqual(out[:8], bytes(8))
        self.assertEqual(out[-4:], bytes((40, 40, 40, 255)))

    def test_background_opaque_interior_and_unmixed_edge(self):
        # 검정이 아닌 회색 전경(40)과 초록 바탕의 50% 혼합은 (20,148,20).
        # 알파를 제거하거나 RGB를 premultiply하면 이 경계 검사가 깨진다.
        rows = [bytes((0, 255, 0, 20, 148, 20, 40, 40, 40))]
        result = cutout.remove_matte(3, 1, 3, rows, (0, 255, 0))[0]
        self.assertEqual(result[:4], bytes(4))
        self.assertEqual(result[8:], bytes((40, 40, 40, 255)))
        for value in result[4:7]:
            self.assertLessEqual(abs(value - 40), 1)
        self.assertLessEqual(abs(result[7] - 128), 1)

    def test_rgba_input_preserves_existing_alpha(self):
        rows = [bytes((0, 255, 0, 255, 40, 40, 40, 120, 40, 40, 40, 255))]
        out = cutout.remove_matte(3, 1, 4, rows, (0, 255, 0))[0]
        self.assertEqual(out[:4], bytes(4))
        self.assertEqual(out[4:8], bytes((40, 40, 40, 120)))

    def test_fit_uses_height_grade_without_distorting_rectangle(self):
        # 직접 계산: 40×140 전경, 머리 y=10, 발 y=150.
        # female 키1063/140을 양축에 적용: 폭304±1, 높이1063.
        rows = [bytes(60 * 4) for _ in range(10)]
        rows += [bytes(10 * 4) + bytes((80, 60, 40, 255)) * 40 + bytes(10 * 4) for _ in range(140)]
        rows += [bytes(60 * 4) for _ in range(10)]
        out = cutout.fit_sprite(60, rows, "mudang_f", 10)
        self.assertEqual(vn.check_sprite(512, 1280, 4, out), [])
        x0, y0, x1, y1 = vn.visible_box(512, out)
        self.assertEqual((y0, y1), (185, 1248))
        self.assertLessEqual(abs((x1 - x0) - 304), 1)

    def test_empty_invalid_head_or_too_wide_rejected(self):
        with self.assertRaises(ValueError):
            cutout.fit_sprite(1, [bytes(4)], "mudang_f", 0)
        with self.assertRaises(ValueError):
            cutout.fit_sprite(1, [bytes((0, 0, 0, 255))], "mudang_f", 1)
        with self.assertRaises(ValueError):
            cutout.fit_sprite(10, [bytes((0, 0, 0, 255)) * 10] * 10, "mudang_f", 0)
        with self.assertRaises(ValueError):
            cutout.fit_sprite(1, [bytes((0, 0, 0, 255))], "mudang_f", float('nan'))

    def test_cli_writes_png_but_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / "raw.png", Path(temp) / "out.png"
            rows = [bytes((0, 255, 0)) * 60 for _ in range(10)]
            rows += [bytes((0, 255, 0)) * 10 + bytes((40, 40, 40)) * 40 + bytes((0, 255, 0)) * 10 for _ in range(140)]
            rows += [bytes((0, 255, 0)) * 60 for _ in range(10)]
            original = png.encode_rgb(60, 160, rows)
            source.write_bytes(original)
            args = [str(source), str(target), "--id", "mudang_f", "--head-top", "10", "--background", "00ff00"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cutout.main(args), 0)
                self.assertEqual(cutout.main([str(source), str(source), *args[2:]]), 2)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(vn.check_sprite(*png.decode(target.read_bytes())), [])
            self.assertLessEqual(target.stat().st_size, 8388608)


if __name__ == "__main__":
    unittest.main()
