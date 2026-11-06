"""E-05-05 임시 sprite·초상(회색 실루엣 + 기준선 + 이름표, 크기·배치 확인용): 결정성, 기준선, 이름표, 글자 비트맵."""
from __future__ import annotations

import unittest

from tools.assets import (art, background_art, build_glyphs, glyphs, make_placeholders as mp, mascot_art, png,
                          portrait_art as pa, sources, vn)
from tools.assets.spec import split_variant

ROOT = sources.ROOT
NAMES = sources.load_npc_labels()
ALL_IDS = ["mudang_f", "mudang_m", *NAMES]
W, H = vn.SPRITE_SIZE


def _alpha(rows, x, y) -> int:
    return rows[y][x * 4 + 3]


class SilhouetteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sprites = {cid: pa.render_sprite(cid, W, H, NAMES.get(cid)) for cid in ALL_IDS}

    def test_covers_all_ids_and_passes_vn_checks(self):
        self.assertEqual(len(ALL_IDS), 13)
        self.assertLessEqual(set(pa.HEADGEAR), set(ALL_IDS))
        for cid, rows in self.sprites.items():
            self.assertEqual(vn.check_sprite(W, H, 4, rows), [], cid)

    def test_baselines(self):
        """등급별 눈 y·평소 표시 경계(640) 점선이 실루엣 위에 있고, 발끝은 FOOT_Y."""
        guide = bytes((*pa.GUIDE, 255))
        for cid, rows in self.sprites.items():
            for y in (pa.eye_y(cid), vn.NORMAL_ROWS):
                self.assertIn(guide, bytes(rows[y]), (cid, y))
            self.assertEqual(vn.visible_box(W, rows)[3], vn.FOOT_Y, cid)
        self.assertEqual(pa.FOOT_Y, vn.FOOT_Y)

    def test_height_follows_grade(self):
        """장식 없는 인물은 보이는 꼭대기 = 등급 머리 꼭대기. 장식이 있어도 꼭대기는 그 위(여백 16 이상)."""
        self.assertEqual(vn.visible_box(W, self.sprites["monk"])[1], vn.HEIGHT_GRADES["male"].top)
        for cid, rows in self.sprites.items():
            top = vn.visible_box(W, rows)[1]
            self.assertLessEqual(top, vn.grade_for(cid).top, cid)
            self.assertGreaterEqual(top, vn.TOP_MARGIN_MIN, cid)
        tops = {g: vn.visible_box(W, self.sprites[c])[1] for c, g in (("hunter", "tall"), ("monk", "male"),
                                                                      ("jumo", "female"), ("boy", "child"))}
        self.assertLess(tops["tall"], tops["male"])
        self.assertLess(tops["female"], tops["child"])

    def test_only_grey_body_and_label_colors(self):
        allowed = {bytes((*c, 255)) for c in (pa.FILL, pa.EDGE, pa.GUIDE, pa.TEXT)} | {bytes(4)}
        for cid, rows in self.sprites.items():
            seen = {bytes(r[i:i + 4]) for r in rows for i in range(0, len(r), 4)}
            self.assertLessEqual(seen, allowed, cid)

    def test_label_inside_body_and_visible_in_normal_and_portrait(self):
        text = bytes((*pa.TEXT, 255))
        for cid, rows in self.sprites.items():
            ys = [y for y, r in enumerate(rows) if text in bytes(r)]
            self.assertTrue(ys, cid)
            top = vn.portrait_top(cid)
            self.assertLess(max(ys), min(vn.NORMAL_ROWS, top + W), cid)    # 평소 표시(0~639)와 초상 크롭 안
            self.assertGreaterEqual(min(ys), max(top, vn.face_box(cid)[3]), cid)   # 얼굴 영역 밖(아래)

    def test_name_changes_only_label(self):
        a = pa.render_sprite("jumo", W, H, "주모")
        b = pa.render_sprite("jumo", W, H, "과부")
        diff = [y for y in range(H) if a[y] != b[y]]
        self.assertTrue(diff)
        lo, hi = pa.label_y("jumo") + glyphs.HEIGHT, pa.label_y("jumo") + 2 * glyphs.HEIGHT + pa.LABEL_GAP
        self.assertTrue(all(lo <= y < hi for y in diff))

    def test_label_fallback_to_id(self):
        self.assertEqual(pa.label_for("jumo", "주모"), "주모")
        self.assertEqual(pa.label_for("mudang_f", None), "무당(여)")
        self.assertEqual(pa.label_for("new_villager", "鬼"), "new_villager")   # 비트맵 없는 글자 → id
        self.assertEqual(pa.label_for("new_villager", None), "new_villager")

    def test_deterministic_and_distinct_headgear(self):
        for cid in ALL_IDS:
            self.assertEqual(pa.render_sprite(cid, W, H, NAMES.get(cid)), self.sprites[cid], cid)
        masks = {tuple(pa.body_mask(cid)) for cid in ALL_IDS}
        self.assertEqual(len(masks), len({(vn.CHARACTER_GRADE[c], pa.HEADGEAR[c]) for c in ALL_IDS}))

    def test_portrait_crop_and_wiring(self):
        for cid in ("hunjang", "mudang_m", "jumo", "boy"):
            rows = pa.render(cid, 512, 512, NAMES.get(cid))
            self.assertEqual(rows, pa.crop_portrait(512, self.sprites[cid], vn.portrait_top(cid)))
            self.assertEqual(art.render("portrait", cid, 512, 512, NAMES.get(cid)).rows, rows)
            self.assertEqual(art.render_rows("sprite", cid, W, H, NAMES.get(cid)), (4, self.sprites[cid]))

    def test_bad_size(self):
        for w, h in ((512, 640), (500, 1250), (0, 0)):
            with self.assertRaises(ValueError):
                pa.render_sprite("jumo", w, h)
        for w, h in ((128, 128), (512, 256)):
            with self.assertRaises(ValueError):
                pa.render("jumo", w, h)

    def test_committed_files_match_generator(self):
        """커밋된 임시 sprite·초상이 지금 생성기와 같은 그림이다(표시가 true인 것만). 해상도·채널·디코드 픽셀로 비교(zlib 차이 허용)."""
        spec = sources.load_spec()
        seen = 0
        labels = dict(spec.expressions)
        for e in sources.load_manifest(ROOT, spec):
            if e.kind not in ("sprite", "portrait") or not e.placeholder:
                continue
            base, expr = split_variant(e.id)
            args = (base, e.width, e.height, NAMES.get(base), labels[expr] if expr else None)
            if e.kind == "sprite":
                data = png.encode_rgba(e.width, e.height, pa.render_sprite(*args))
            else:
                data = png.encode_rgb(e.width, e.height, pa.render(*args))
            self.assertTrue(png.same_image((ROOT / e.path).read_bytes(), data), e.path)
            seen += 1
        self.assertEqual(seen, 74)          # 인물 13 + 표정 변형 24, sprite·초상 각각


class CacheTest(unittest.TestCase):
    """그린 결과 기억(가속): 복사본 반환, 표 값이 바뀌면 다시 그림, 합성 결과는 픽셀마다 계산한 것과 같음."""

    def test_returns_copies(self):
        a = pa.render_sprite("jumo", W, H)
        a[700][0:4] = b""
        self.assertNotEqual(pa.render_sprite("jumo", W, H)[700][0:4], b"")
        m = mascot_art.render("kkachi", 512, 512)
        m[0][0:4] = b""
        self.assertNotEqual(mascot_art.render("kkachi", 512, 512)[0][0:4], b"")

    def test_table_change_redraws(self):
        from unittest import mock
        before = pa.render_sprite("jumo", W, H)
        with mock.patch.dict(vn.CHARACTER_GRADE, {"jumo": "male"}):
            self.assertNotEqual(pa.render_sprite("jumo", W, H), before)
            with mock.patch.dict(pa.HEADGEAR, {"jumo": None}):   # 장식 없이 보면 머리 꼭대기가 바뀐 등급 값
                self.assertEqual(vn.visible_box(W, pa.render_sprite("jumo", W, H))[1], vn.HEIGHT_GRADES["male"].top)
        self.assertEqual(pa.render_sprite("jumo", W, H), before)
        with mock.patch.dict(pa.HEADGEAR, {"jumo": None}):
            self.assertNotEqual(pa.render_sprite("jumo", W, H), before)
        m = mascot_art.render("kkachi", 512, 512)
        with mock.patch.dict(mascot_art.OFFSET, {"kkachi": (0, 0)}):
            self.assertNotEqual(mascot_art.render("kkachi", 512, 512), m)
        with mock.patch.dict(vn.CHARACTER_GRADE, {"jumo": "male"}):
            moved = mp._placeholder_png("portrait", "jumo", 512, 512, None, None)
        self.assertNotEqual(moved, mp._placeholder_png("portrait", "jumo", 512, 512, None, None))

    def test_mask_composite_matches_per_pixel(self):
        bg = pa.BG
        row = bytearray()
        for x in range(512):
            a = (0, 255, 1, 128, 254)[x % 5]
            row += bytes((x % 256, (x * 7) % 256, (x * 13) % 256, a))
        want = bytearray()
        for x in range(512):
            r, g, b, a = row[x * 4:x * 4 + 4]
            want += bytes((c * a + bg[i] * (255 - a) + 127) // 255 for i, c in enumerate((r, g, b)))
        self.assertEqual(pa.crop_portrait(512, [row] * 512, 0)[0], want)


class GlyphTest(unittest.TestCase):
    def test_all_needed_chars_committed(self):
        """콘텐츠 묶음의 이름·고정 문구 글자가 모두 비트맵에 있다(없으면 build_glyphs를 다시 돌린다)."""
        missing = [ch for ch in build_glyphs.needed_chars() if ch not in glyphs.GLYPHS]
        self.assertEqual(missing, [])

    def test_draw_and_width(self):
        rows = [bytearray(3 * 200) for _ in range(glyphs.HEIGHT * 2)]
        glyphs.draw(rows, 3, 0, 0, "임시", (255, 0, 0), scale=2)
        self.assertTrue(any(b"\xff\x00\x00" in bytes(r) for r in rows))
        self.assertEqual(glyphs.width("임 시", 2), 2 * (glyphs.GLYPHS["임"][0] + glyphs.SPACE + glyphs.GLYPHS["시"][0]))
        self.assertFalse(glyphs.fits("鬼"))
        glyphs.draw(rows, 3, 190, -5, "鬼임", (0, 0, 255))       # 없는 글자·가장자리 넘김은 건너뛴다

    def test_background_labels(self):
        for cid in background_art.IDS:
            self.assertTrue(glyphs.fits(background_art.LABELS[cid]), cid)


if __name__ == "__main__":
    unittest.main()
