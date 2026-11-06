"""VN 사전 작업(크로스체크 17·18): 전신 sprite·장면 배경 검사, 초상 파생(알파 합성), 외형의 공개 ID 고정,
평소/전신 컷 배치와 검수용 합성, PNG 읽기·필터 선택."""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import shutil
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tools.assets import art, background_art, png, portrait_art as pa, preview_vn, sources, vn
from tools.assets.make_placeholders import format_report, run
from tools.assets.spec import AssetEntry, pair_mismatches

ALL_IDS = list(pa.HEADGEAR)
W, H = vn.SPRITE_SIZE


def _sprite(cid: str = "jumo") -> list[bytearray]:
    return pa.render_sprite(cid, W, H)


def _shift(rows: list[bytearray], dx: int = 0, dy: int = 0) -> list[bytearray]:
    """sprite를 px 단위로 옮긴다(빈 곳은 투명)."""
    out = [bytearray(W * 4) for _ in range(H)]
    for y in range(H):
        sy = y - dy
        if not 0 <= sy < H:
            continue
        src = rows[sy]
        if dx >= 0:
            out[y][dx * 4:] = src[:(W - dx) * 4]
        else:
            out[y][:(W + dx) * 4] = src[-dx * 4:]
    return out


def _with_alpha(rows: list[bytearray], alpha: int) -> list[bytearray]:
    """보이는 픽셀의 알파를 모두 `alpha`로(반투명 sprite)."""
    out = []
    for r in rows:
        r = bytearray(r)
        for i in range(3, len(r), 4):
            if r[i]:
                r[i] = alpha
        out.append(r)
    return out


class SpriteCheckTest(unittest.TestCase):
    def test_all_generated_sprites_pass(self):
        for cid in ALL_IDS:
            self.assertEqual(vn.check_sprite(W, H, 4, _sprite(cid)), [], cid)

    def test_semi_transparent_and_any_color_allowed(self):
        """반실사: 8비트 반투명·팔레트 밖 색·블록 불일치는 실패가 아니다."""
        rows = _with_alpha(_sprite(), 128)
        rows[300][256 * 4:256 * 4 + 4] = bytes((1, 2, 3, 77))
        self.assertEqual(vn.check_sprite(W, H, 4, rows), [])

    def test_composition_rules(self):
        self.assertTrue(any("가운데" in p for p in vn.check_sprite(W, H, 4, _shift(_sprite(), dx=24))))
        self.assertEqual(vn.check_sprite(W, H, 4, _shift(_sprite(), dx=12)), [])
        top = vn.visible_box(W, _sprite("hunjang"))[1]
        self.assertTrue(any("머리 위" in p for p in vn.check_sprite(W, H, 4, _shift(_sprite("hunjang"), dy=-(top - 8)))))
        self.assertTrue(any("발끝" in p for p in vn.check_sprite(W, H, 4, _shift(_sprite(), dy=-40))))  # 떠 있음
        self.assertTrue(any("발끝" in p for p in vn.check_sprite(W, H, 4, _shift(_sprite(), dy=24))))   # 잘림

    def test_faint_noise_ignored_but_visible_speck_counts(self):
        rows = _sprite()
        rows[2][0:4] = bytes((9, 9, 9, vn.VISIBLE_ALPHA - 1))          # 배경 제거 뒤 남은 옅은 점
        self.assertEqual(vn.check_sprite(W, H, 4, rows), [])
        rows[2][0:4] = bytes((9, 9, 9, vn.VISIBLE_ALPHA))
        self.assertTrue(vn.check_sprite(W, H, 4, rows))

    def test_size_and_channels(self):
        self.assertTrue(vn.check_sprite(512, 640, 4, _sprite()[:640]))
        rgb = [bytearray(b"".join(bytes(r[i:i + 3]) for i in range(0, len(r), 4))) for r in _sprite()]
        self.assertEqual(vn.check_sprite(W, H, 3, rgb), ["투명 채널 없음(RGBA로 저장)"])

    def test_baselines_of_placeholder_art(self):
        """임시 그림은 키 등급으로 그린다: 눈 점선 = 등급 눈 y, 발끝 y=1248."""
        guide = bytes((*pa.GUIDE, 255))
        for cid in ALL_IDS:
            self.assertIn(guide, bytes(_sprite(cid)[pa.eye_y(cid)]), cid)
            self.assertEqual(vn.visible_box(W, _sprite(cid))[3], vn.FOOT_Y, cid)


class HeightGradeTest(unittest.TestCase):
    """키 등급(11/6 PM 결정): 같은 축척, 인물별 머리 꼭대기·등신, 얼굴 영역·초상 크롭은 등급에서 계산."""

    def test_grade_values(self):
        got = {g.name: (g.top, g.heads, g.portrait_eye) for g in vn.HEIGHT_GRADES.values()}
        self.assertEqual(got, {"tall": (24, 7.2, 200), "male": (94, 7.0, 200), "female": (185, 6.8, 200),
                               "child": (339, 6.0, 230)})
        g = vn.HEIGHT_GRADES["male"]
        self.assertAlmostEqual(g.head, (1248 - 94) / 7)
        self.assertAlmostEqual(g.eye_y, 94 + g.head / 2)

    def test_every_character_has_grade(self):
        self.assertEqual(set(vn.CHARACTER_GRADE), set(ALL_IDS))
        self.assertLessEqual(set(vn.CHARACTER_GRADE.values()), set(vn.HEIGHT_GRADES))
        with self.assertRaises(ValueError):
            vn.grade_for("new_villager")
        with self.assertRaises(ValueError):
            vn.face_box("hunjang__joy")                 # 표정 접미사는 호출 쪽이 뗀다

    def test_face_box_and_portrait_top(self):
        want = {"hunter": ((154, 7, 358, 226), 0), "hunjang": ((157, 78, 355, 290), 0),
                "jumo": ((162, 169, 350, 371), 63), "boy": ((165, 324, 347, 519), 185)}
        for cid, (box, top) in want.items():
            self.assertEqual((vn.face_box(cid), vn.portrait_top(cid)), (box, top), cid)

    def test_portrait_top_clamped(self):
        from unittest import mock
        extra = {"low": vn.HeightGrade("low", 1100, 1.0, 0), "high": vn.HeightGrade("high", 16, 7.0, 400)}
        with mock.patch.dict(vn.HEIGHT_GRADES, extra), mock.patch.dict(vn.CHARACTER_GRADE, {"x": "low", "y": "high"}):
            self.assertEqual(vn.portrait_top("x"), H - 512)
            self.assertEqual(vn.portrait_top("y"), 0)

    def test_round_half_up(self):
        self.assertEqual((vn._round(2.5), vn._round(3.5), vn._round(-0.5)), (3, 4, 0))


class BackgroundTest(unittest.TestCase):
    def test_both_pass_and_transparency_fails(self):
        for cid in background_art.IDS:
            rows = background_art.render(cid, 1280, 720)
            self.assertEqual(vn.check_background(1280, 720, 3, rows), [], cid)
            rgba = [bytearray(b"".join(bytes(r[i:i + 3]) + b"\xff" for i in range(0, len(r), 3))) for r in rows]
            self.assertEqual(vn.check_background(1280, 720, 4, rgba), [], cid)       # 불투명 RGBA는 통과
        rgba[5][3] = 200
        self.assertTrue(any("불투명" in p for p in vn.check_background(1280, 720, 4, rgba)))
        self.assertTrue(vn.check_background(1280, 700, 3, rows[:700]))

    def test_label_top_left_and_plain_elsewhere(self):
        """이름표는 왼쪽 위에만 있고, 가운데(sprite 자리)와 아래 1/4(대사창)은 행마다 한 색(그라데이션)."""
        for cid in background_art.IDS:
            rows = background_art.render(cid, 1280, 720)
            ink = bytes(background_art.COLORS[cid][2])
            self.assertIn(ink, bytes(rows[background_art.LABEL_XY[1] + 40]))
            for y in list(range(200, 720, 37)):
                r = bytes(rows[y])
                self.assertEqual(r, r[:3] * 1280, (cid, y))

    def test_unknown_id_and_bad_size(self):
        with self.assertRaises(ValueError):
            background_art.render("tavern", 1280, 720)
        with self.assertRaises(ValueError):
            background_art.render("night", 1280, 700)


class PortraitDerivationTest(unittest.TestCase):
    def test_portrait_is_grade_crop(self):
        for cid in ALL_IDS:
            self.assertEqual(pa.render(cid, 512, 512), pa.crop_portrait(512, _sprite(cid), vn.portrait_top(cid)), cid)

    def test_crop_range_checked(self):
        rows = _sprite()
        with self.assertRaises(ValueError):
            pa.crop_portrait(512, rows, H - 511)
        with self.assertRaises(ValueError):
            pa.crop_portrait(512, rows, -1)
        self.assertEqual(len(pa.crop_portrait(512, rows, H - 512)), 512)

    def test_alpha_composite_over_ink_navy(self):
        """알파 0 → 먹남, 255 → 원색, 128 → 반씩 섞음(반올림)."""
        row = bytearray()
        for a in (0, 128, 255):
            row += bytes((200, 100, 0, a))
        rows = [row + bytes(4 * 509)] + [bytearray(512 * 4)] * 511
        out = pa.crop_portrait(512, rows, 0)[0]
        bg = pa.BG
        self.assertEqual(bytes(out[0:3]), bytes(bg))
        self.assertEqual(bytes(out[3:6]), bytes((200 * 128 + c * 127 + 127) // 255 for c in bg)[:0]
                         + bytes(((200 * 128 + bg[0] * 127 + 127) // 255, (100 * 128 + bg[1] * 127 + 127) // 255,
                                  (0 * 128 + bg[2] * 127 + 127) // 255)))
        self.assertEqual(bytes(out[6:9]), bytes((200, 100, 0)))

    def test_pair_mismatch(self):
        p = AssetEntry("portrait", "boy", "r/portrait/boy.png", "/u", 512, 512, placeholder=False,
                       derived_from="sprite:boy")
        s = AssetEntry("sprite", "boy", "r/sprite/boy.png", "/u", W, H, placeholder=True)
        self.assertEqual(len(pair_mismatches([p, s])), 1)
        self.assertEqual(pair_mismatches([p, AssetEntry(**{**s.__dict__, "placeholder": False})]), [])
        self.assertIn("없음", pair_mismatches([p])[0])


class PublicIdOnlyTest(unittest.TestCase):
    """외형은 공개 npc_id에만 고정한다(크로스체크 17 지적 9). 역할·성격·일과·순서가 바뀌어도 같은 바이트."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_shape_takes_only_id(self):
        self.assertEqual(list(inspect.signature(pa.body_mask).parameters), ["cid"])

    def test_bundle_fields_other_than_id_do_not_change_images(self):
        a, b = self.tmp / "a", self.tmp / "b"
        run(a)
        bundle = json.loads(sources.BUNDLE_PATH.read_text(encoding="utf-8"))
        temperaments = [c["temperament_id"] for c in bundle["characters"]]
        for c, t in zip(bundle["characters"], reversed(temperaments)):
            c["temperament_id"] = t                 # 성격·일과를 바꾸고 순서를 뒤집는다(이름표는 공개 표시 이름이라 그대로)
            c["routine"] = {}
        bundle["characters"].reverse()
        changed = self.tmp / "bundle.json"
        changed.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        run(b, bundle_path=changed)
        for kind in ("sprite", "portrait"):
            for p in sorted((a / "content/images" / kind).iterdir()):
                self.assertEqual(p.read_bytes(), (b / "content/images" / kind / p.name).read_bytes(), p.name)

    def test_night_has_no_sprite(self):
        self.assertEqual(vn.NIGHT_SPRITES, 0)
        for mode in vn.MODES:
            with self.assertRaises(preview_vn.PreviewError):
                preview_vn.build(sources.ROOT, "night", "hunjang", mode)


class RealSpriteFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        run(self.tmp)
        self.mpath = self.tmp / "content/images/manifest.json"

    def _mark(self, kind: str, cid: str, placeholder: bool) -> dict:
        items = json.loads(self.mpath.read_text(encoding="utf-8"))
        d = next(x for x in items if x["kind"] == kind and x["id"] == cid)
        d["placeholder"] = placeholder
        self.mpath.write_text(json.dumps(items), encoding="utf-8")
        return d

    def test_real_semi_transparent_sprite_rederives_portrait_and_flips_flag(self):
        d = self._mark("sprite", "hunjang", False)
        real = png.encode_rgba(W, H, _with_alpha(_shift(_sprite("jumo"), dx=4), 200))
        (self.tmp / d["path"]).write_bytes(real)
        r = run(self.tmp)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual(r.kept_real, 1)
        self.assertEqual((self.tmp / d["path"]).read_bytes(), real)
        portrait = (self.tmp / "content/images/portrait/hunjang.png").read_bytes()
        want = png.encode_rgb(512, 512, pa.crop_portrait(512, png.decode(real)[3], vn.portrait_top("hunjang")),
                               optimize=True)
        self.assertEqual(portrait, want)
        items = json.loads(self.mpath.read_text(encoding="utf-8"))
        p = next(x for x in items if x["kind"] == "portrait" and x["id"] == "hunjang")
        self.assertEqual((p["placeholder"], p["derived_from"]), (False, "sprite:hunjang"))

    def test_real_portrait_with_placeholder_sprite_is_kept_but_reported(self):
        d = self._mark("portrait", "boy", False)
        real = png.encode_rgb(512, 512, art.render("map", "real", 512, 512).rows)
        (self.tmp / d["path"]).write_bytes(real)
        r = run(self.tmp)
        self.assertFalse(r.ok)
        self.assertEqual((self.tmp / d["path"]).read_bytes(), real)
        self.assertEqual(len(r.recon.pair_mismatch), 1)
        self.assertTrue(any("크롭과 다름" in b for b in r.bad_files))

    def test_force_keeps_real_portrait_of_real_sprite(self):
        """sprite·초상이 모두 실제 표시면 --force도 초상을 덮어쓰지 않는다(Codex 리뷰 P1)."""
        self._mark("sprite", "hunjang", False)
        d = self._mark("portrait", "hunjang", False)
        real = png.encode_rgb(512, 512, art.render("map", "real", 512, 512).rows)
        (self.tmp / d["path"]).write_bytes(real)
        r = run(self.tmp, force=True)
        self.assertEqual((self.tmp / d["path"]).read_bytes(), real)
        self.assertIn(d["path"], r.protected_files)

    def test_rgba_portrait_reported(self):
        """RGBA 초상은 크롭 비교를 건너뛰지 않고 위반으로 보고한다(Codex 리뷰 P2)."""
        d = self._mark("portrait", "boy", False)
        self._mark("sprite", "boy", False)
        (self.tmp / d["path"]).write_bytes(png.encode_rgba(512, 512, _sprite("boy")[:512]))
        r = run(self.tmp, write=False)
        self.assertTrue(any("portrait/boy.png" in b and "RGB" in b for b in r.bad_files), r.bad_files)

    def test_bad_real_sprite_reported(self):
        d = self._mark("sprite", "monk", False)
        (self.tmp / d["path"]).write_bytes(png.encode_rgba(W, 640, _sprite("monk")[:640]))
        with self.assertRaises(Exception):           # 규격 밖 실제 sprite는 초상 파생을 멈춘다
            run(self.tmp)
        r = run(self.tmp, write=False)
        self.assertTrue(any("sprite/monk.png" in b for b in r.bad_files))

    def test_rederive_only_touches_placeholder_portraits(self):
        jumo = self.tmp / "content/images/portrait/jumo.png"
        old = png.encode_rgb(512, 512, art.render("map", "old", 512, 512).rows)
        jumo.write_bytes(old)
        ui = self.tmp / "content/images/ui/vote.png"
        ui_old = png.encode_rgb(128, 128, art.render("map", "x", 128, 128).rows)
        ui.write_bytes(ui_old)
        r = run(self.tmp, rederive_portraits=True)
        self.assertEqual(r.protected_files, ["content/images/ui/vote.png"])
        self.assertNotEqual(jumo.read_bytes(), old)
        self.assertEqual(ui.read_bytes(), ui_old)


class LayoutTest(unittest.TestCase):
    def test_mode_numbers(self):
        """평소: 머리~허리 0.8배 410×512, 아랫변 = 대사창 윗변 540. 전신 컷: 0.5배 256×640, 위 40, 대사창 숨김."""
        self.assertEqual(vn.place(vn.NORMAL), (435, 28, 845, 540))
        self.assertEqual(vn.dialogue_box()[1], 540)
        self.assertEqual(vn.NORMAL.rows, (0, vn.NORMAL_ROWS))
        self.assertEqual(vn.place(vn.FULL), (512, 40, 768, 680))
        self.assertFalse(vn.FULL.dialogue)

    def test_resize_keeps_opaque_and_transparent_and_smooths_edges(self):
        rows = [bytearray(bytes((10, 20, 30, 255)) * 4 + bytes(4 * 4)) for _ in range(8)]
        small = vn.resize_rgba(rows, 8, (4, 4))
        self.assertEqual(bytes(small[0][0:4]), bytes((10, 20, 30, 255)))
        self.assertEqual(small[0][3 * 4 + 3], 0)
        half = vn.resize_rgba(rows, 8, (3, 4))[0][1 * 4:2 * 4]     # 경계에 걸친 칸: 색은 그대로, 알파만 줄어듦
        self.assertEqual(bytes(half[:3]), bytes((10, 20, 30)))
        self.assertTrue(0 < half[3] < 255)


class PreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        run(cls.tmp)
        (cls.tmp / "docs/assets").mkdir(parents=True)
        shutil.copy(sources.SPEC_PATH, cls.tmp / "docs/assets/spec.md")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def _main(self, argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return preview_vn.main(["--repo", str(self.tmp), *argv])

    def test_normal_and_full_layout(self):
        bg = background_art.render("meeting", 1280, 720)
        out = self.tmp / "out/n.png"
        self.assertEqual(self._main(["--bg", "meeting", "--id", "hunjang", "--out", str(out)]), 0)
        w, h, ch, rows = png.decode(out.read_bytes())
        self.assertEqual((w, h, ch), (1280, 720, 3))
        self.assertEqual(rows[600][640 * 3:640 * 3 + 3], vn.DIALOGUE_FILL)     # 대사창이 아래 180px를 덮음
        self.assertEqual(rows[10], bg[10])                                       # sprite 위는 배경 그대로
        self.assertNotEqual(rows[530][640 * 3:640 * 3 + 3], bg[530][640 * 3:640 * 3 + 3])  # 대사창 바로 위까지 몸
        out = self.tmp / "out/f.png"
        self.assertEqual(self._main(["--bg", "meeting", "--id", "hunjang", "--mode", "full", "--out", str(out)]), 0)
        rows = png.decode(out.read_bytes())[3]
        self.assertNotEqual(rows[600][640 * 3:640 * 3 + 3], vn.DIALOGUE_FILL)  # 전신 컷은 대사창 숨김
        self.assertEqual(rows[700], bg[700])                                     # 발 아래는 배경

    def test_out_path_rules_and_night(self):
        self.assertEqual(self._main(["--bg", "meeting", "--out", str(self.tmp / "docs/p.png")]), 2)
        self.assertEqual(self._main(["--bg", "night", "--id", "boy", "--out", str(self.tmp / "out/n.png")]), 2)
        self.assertEqual(self._main(["--bg", "meeting", "--mode", "x", "--out", str(self.tmp / "out/x.png")]), 2)
        self.assertEqual(self._main(["--bg", "night", "--out", str(self.tmp / "out/night.png")]), 0)
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, True)
        self.assertEqual(self._main(["--bg", "meeting", "--out", str(outside / "m.png")]), 0)


class PngTest(unittest.TestCase):
    def test_decode_all_filter_types(self):
        """필터 0~4로 직접 만든 RGBA PNG를 원래 픽셀로 되돌린다(외부 도구 출력 대비)."""
        w, h, ch = 5, 5, 4
        rows = [b"".join(bytes((x * 40 + y, y * 30, (x * y * 7) % 256, 255 if (x + y) % 2 else 0))
                         for x in range(w)) for y in range(h)]

        def paeth(a, b, c):
            p = a + b - c
            pa_, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            return a if pa_ <= pb and pa_ <= pc else (b if pb <= pc else c)

        raw, prev = b"", bytes(w * ch)
        for y, r in enumerate(rows):
            f = y % 5
            enc = bytearray()
            for i in range(len(r)):
                a = r[i - ch] if i >= ch else 0
                b = prev[i]
                c = prev[i - ch] if i >= ch else 0
                pred = (0, a, b, (a + b) // 2, paeth(a, b, c))[f]
                enc.append((r[i] - pred) & 0xFF)
            raw += bytes([f]) + bytes(enc)
            prev = r
        data = (png.SIGNATURE + png._chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + png._chunk(b"IDAT", zlib.compress(raw)) + png._chunk(b"IEND", b""))
        self.assertEqual(png.decode(data), (w, h, 4, rows))

    def test_optimized_encoding_roundtrips_and_is_smaller_on_gradients(self):
        rows = [bytes(((x + y) % 256, (x * 2) % 256, y % 256) * 1) for y in range(64) for x in range(1)]
        rows = [b"".join(bytes(((x + y) % 256, (x * 2 + y) % 256, (y * 3) % 256)) for x in range(64)) for y in range(64)]
        plain, opt = png.encode_rgb(64, 64, rows), png.encode_rgb(64, 64, rows, optimize=True)
        self.assertEqual(png.decode(opt)[3], rows)
        self.assertLess(len(opt), len(plain))

    def test_rgba_roundtrip_and_unsupported(self):
        rows = _sprite("boy")
        self.assertEqual(png.decode(png.encode_rgba(W, H, rows))[3], [bytes(r) for r in rows])
        gray = (png.SIGNATURE + png._chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
                + png._chunk(b"IDAT", zlib.compress(b"\x00\x00")) + png._chunk(b"IEND", b""))
        with self.assertRaises(ValueError):
            png.decode(gray)


if __name__ == "__main__":
    unittest.main()
