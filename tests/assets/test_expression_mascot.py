"""표정 변형·마스코트(11/5 PM 결정 P-20·P-21, 크로스체크 19): 규격표 8·9절 해석, 기대 항목, 얼굴 영역 검사,
기본 sprite 교체 감지, 마스코트 검사, 검수용 합성."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.assets import mascot_art, png, portrait_art as pa, preview_vn, sources, vn
from tools.assets.make_placeholders import format_report, run
from tools.assets.spec import SpecError, parse_spec_md, split_variant, variant_id

SPEC_TEXT = sources.SPEC_PATH.read_text(encoding="utf-8")
W, H = vn.SPRITE_SIZE
FX0, FY0, FX1, FY1 = vn.FACE_BOX


def _expected():
    from tools.assets.make_placeholders import build_expected
    spec = parse_spec_md(SPEC_TEXT)
    return spec, build_expected(spec, sources.BUNDLE_PATH, sources.CAMPAIGNS_PATH)


def _paint_face(rows, color=(200, 40, 40, 255)):
    """얼굴 영역 가운데 20×20을 칠한 사본(편집 결과의 얼굴만 붙여 넣은 표정 sprite 흉내)."""
    out = [bytearray(r) for r in rows]
    cx, cy = (FX0 + FX1) // 2, (FY0 + FY1) // 2
    for y in range(cy - 10, cy + 10):
        for x in range(cx - 10, cx + 10):
            out[y][x * 4:x * 4 + 4] = bytes(color)
    return out


class SpecTest(unittest.TestCase):
    def test_expressions_and_targets_from_spec(self):
        spec = parse_spec_md(SPEC_TEXT)
        self.assertEqual(spec.expressions, (("joy", "기쁨"), ("flustered", "당황"), ("sad", "슬픔")))
        self.assertEqual(spec.expression_targets[:2], ("mudang_f", "mudang_m"))

    def test_unknown_target_rejected(self):
        bad = SPEC_TEXT.replace("| meosum |", "| nobody |")
        spec = parse_spec_md(bad)
        from tools.assets.spec import expected_entries
        with self.assertRaises(SpecError):
            expected_entries(spec, sources.load_npc_ids(), [], [], {}, lambda a, b: a)

    def test_variant_entries_and_edited_from(self):
        spec, entries = _expected()
        sprites = {e.id: e for e in entries if e.kind == "sprite"}
        portraits = {e.id: e for e in entries if e.kind == "portrait"}
        for t in spec.expression_targets:
            for ex, _ in spec.expressions:
                vid = variant_id(t, ex)
                self.assertEqual(sprites[vid].path, f"content/images/sprite/{t}__{ex}.png")
                self.assertEqual(sprites[vid].edited_from, f"sprite:{t}")
                self.assertEqual(portraits[vid].derived_from, f"sprite:{vid}")
        self.assertIsNone(sprites["hunjang"].edited_from)
        self.assertNotIn(variant_id("boy", "joy"), sprites)          # 확장 주민 풀은 기본 1장
        self.assertEqual(split_variant("hunjang__joy"), ("hunjang", "joy"))
        self.assertEqual(split_variant("hunjang"), ("hunjang", None))
        mascots = sorted(e.id for e in entries if e.kind == "mascot")
        self.assertEqual(mascots, ["jokjebi", "kkachi"])


class ExpressionCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = pa.render_sprite("hunjang", W, H, "훈장")

    def test_placeholder_variants_pass(self):
        for label in ("기쁨", "당황", "슬픔"):
            self.assertEqual(vn.check_expression(self.base, pa.render_sprite("hunjang", W, H, "훈장", label)), [])

    def test_face_only_change_passes(self):
        self.assertEqual(vn.check_expression(self.base, _paint_face(self.base)), [])

    def test_identical_copy_fails(self):
        msgs = vn.check_expression(self.base, [bytearray(r) for r in self.base])
        self.assertTrue(any("변화 없음" in m for m in msgs), msgs)

    def test_single_pixel_outside_face_fails(self):
        v = _paint_face(self.base)
        v[700][256 * 4:256 * 4 + 4] = bytes((1, 2, 3, 255))     # 몸통 한 점
        msgs = vn.check_expression(self.base, v)
        self.assertTrue(any("밖 1픽셀" in m for m in msgs), msgs)

    def test_invisible_rgb_change_ignored(self):
        v = _paint_face(self.base)
        v[5][0:4] = bytes((9, 9, 9, 0))                          # 완전 투명 픽셀의 RGB만 다름
        self.assertEqual(vn.check_expression(self.base, v), [])


class MascotCheckTest(unittest.TestCase):
    def test_generated_mascots_pass(self):
        for cid in mascot_art.SHAPES:
            rows = mascot_art.render(cid, 512, 512)
            self.assertEqual(vn.check_mascot(512, 512, 4, rows), [], cid)

    def test_opaque_square_with_clear_corners_fails(self):
        rows = [bytearray(bytes((90, 90, 90, 255)) * 512) for _ in range(512)]
        for y in (0, 511):
            for x in (0, 511):
                rows[y][x * 4 + 3] = 0
        msgs = vn.check_mascot(512, 512, 4, rows)
        self.assertTrue(any("투명 픽셀" in m for m in msgs), msgs)

    def test_size_channel_and_corner(self):
        rows = mascot_art.render("kkachi", 512, 512)
        self.assertTrue(vn.check_mascot(512, 256, 4, rows[:256]))
        self.assertTrue(vn.check_mascot(512, 512, 3, rows))
        rows[0][3] = 255
        self.assertTrue(any("모서리" in m for m in vn.check_mascot(512, 512, 4, rows)))

    def test_unknown_mascot_rejected(self):
        with self.assertRaises(ValueError):
            mascot_art.render("tiger", 512, 512)


class RealExpressionFlowTest(unittest.TestCase):
    """실제 기본 + 임시 표정은 정상, 실제 표정은 기본 SHA를 기록하고 기본이 바뀌면 위반."""

    def test_flow(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        run(tmp)
        mpath = tmp / "content/images/manifest.json"

        def mark(cid, placeholder):
            items = json.loads(mpath.read_text(encoding="utf-8"))
            d = next(x for x in items if x["kind"] == "sprite" and x["id"] == cid)
            d["placeholder"] = placeholder
            mpath.write_text(json.dumps(items), encoding="utf-8")
            return tmp / d["path"]

        base_rows = pa.render_sprite("jumo", W, H)                        # "실제" 기본(다른 그림)
        base_path = mark("hunjang", False)
        base_bytes = png.encode_rgba(W, H, base_rows)
        base_path.write_bytes(base_bytes)
        r = run(tmp)
        self.assertTrue(r.ok, format_report(r))                           # 표정은 아직 임시: 중간 상태 허용

        var_path = mark("hunjang__joy", False)
        var_path.write_bytes(png.encode_rgba(W, H, _paint_face(base_rows)))
        r = run(tmp)
        self.assertTrue(r.ok, format_report(r))
        items = json.loads(mpath.read_text(encoding="utf-8"))
        v = next(x for x in items if x["kind"] == "sprite" and x["id"] == "hunjang__joy")
        self.assertEqual(v["edited_from"], "sprite:hunjang")
        self.assertEqual(v["edited_from_sha256"], hashlib.sha256(base_bytes).hexdigest())
        p = next(x for x in items if x["kind"] == "portrait" and x["id"] == "hunjang__joy")
        self.assertIs(p["placeholder"], False)                            # 표정 초상도 파생

        base_path.write_bytes(png.encode_rgba(W, H, _paint_face(base_rows, (10, 200, 10, 255))))   # 기본 얼굴만 교체
        r = run(tmp, write=False)
        self.assertTrue(any("hunjang__joy" in b and "바뀜" in b for b in r.bad_files), r.bad_files)

    def test_wrong_size_pair_reported_not_crash(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        run(tmp)
        mpath = tmp / "content/images/manifest.json"
        items = json.loads(mpath.read_text(encoding="utf-8"))
        small = [bytearray(r[:256 * 4]) for r in pa.render_sprite("hunjang", W, H)]
        varied = [bytearray(r) for r in small]
        varied[200][200 * 4:200 * 4 + 4] = bytes((200, 40, 40, 255))
        for cid, rows in (("hunjang", small), ("hunjang__sad", varied)):
            d = next(x for x in items if x["kind"] == "sprite" and x["id"] == cid)
            d["placeholder"] = False
            (tmp / d["path"]).write_bytes(png.encode_rgba(256, H, rows))
        mpath.write_text(json.dumps(items), encoding="utf-8")
        r = run(tmp, write=False)                                         # IndexError 없이 위반 보고
        self.assertTrue(any("hunjang__sad" in b and "크기" in b for b in r.bad_files), r.bad_files)



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

    def test_expr_and_mascot(self):
        out = self.tmp / "out/m.png"
        self.assertEqual(self._main(["--bg", "meeting", "--id", "hunjang", "--expr", "sad", "--mascot", "kkachi",
                                     "--out", str(out)]), 0)
        rows = png.decode(out.read_bytes())[3]
        mx, my, (mw, mh) = vn.MASCOT
        bg = png.decode((self.tmp / "content/images/background/meeting.png").read_bytes())[3]
        changed = sum(rows[y][x * 3:x * 3 + 3] != bg[y][x * 3:x * 3 + 3]
                      for y in range(my, my + mh, 8) for x in range(mx, mx + mw, 8))
        self.assertGreater(changed, 0)                                    # 마스코트 자리에 그림이 있다

    def test_rejections(self):
        out = str(self.tmp / "out/x.png")
        self.assertEqual(self._main(["--bg", "night", "--mascot", "kkachi", "--out", out]), 2)
        self.assertEqual(self._main(["--bg", "meeting", "--mascot", "tiger", "--out", out]), 2)
        self.assertEqual(self._main(["--bg", "meeting", "--id", "boy", "--expr", "joy", "--out", out]), 2)  # 파일 없음


if __name__ == "__main__":
    unittest.main()
