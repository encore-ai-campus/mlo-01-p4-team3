"""E-05-05 임시 에셋: 규격표 해석, 기대 항목(102, VN sprite·배경·표정 변형·마스코트 포함), 생성·대조, 실제 파일 보존."""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from ads.domain.model import SLOT_SIZES, AdSlot, slot_image_path
from tools.assets import art, png, portrait_art, sources, vn
from tools.assets.make_placeholders import format_report, main, result_metrics, run
from tools.assets.spec import (AssetEntry, SpecError, ad_entries, expected_entries, parse_spec_md,
                               reconcile)

ROOT = sources.ROOT
SPEC_TEXT = sources.SPEC_PATH.read_text(encoding="utf-8")
UI_IDS = ("divination_clear", "divination_murky", "geumjul", "vote", "suspect",
          "defend", "interrogate", "confess", "missing", "notebook")
SLOT_SIZES_STR = {s.value: v for s, v in SLOT_SIZES.items()}


def _quiet_main(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return main(argv)


def _recompress(data: bytes, level: int) -> bytes:
    """같은 PNG를 다른 zlib 압축 수준으로 다시 묶는다(픽셀 같음, 바이트 다름). zlib 구현 차이 재현용."""
    import zlib
    ihdr = data[16:16 + 13]
    return (png.SIGNATURE + png._chunk(b"IHDR", ihdr) + png._chunk(b"IDAT", zlib.compress(zlib.decompress(b"".join(
        body for tag, body in png._chunks(data) if tag == b"IDAT")), level))
            + png._chunk(b"IEND", b""))


def _expected():
    spec = parse_spec_md(SPEC_TEXT)
    campaigns, slot_sizes = sources.load_campaigns()
    return spec, expected_entries(spec, sources.load_npc_ids(), sources.load_place_ids(),
                                  campaigns, slot_sizes, sources.slot_image_path)


class SpecTableTest(unittest.TestCase):
    def test_bad_kind_limit_row_rejected(self):
        bad = SPEC_TEXT.replace("| 용량 상한(바이트) sprite | 8388608 |", "| 용량 상한(바이트) sprit | 8388608 |")
        with self.assertRaises(SpecError):
            parse_spec_md(bad)

    def test_values_match_task_condition_1(self):
        spec = parse_spec_md(SPEC_TEXT)
        self.assertEqual(spec.image_format, "png")
        self.assertEqual(spec.max_bytes, 512000)
        self.assertEqual(spec.kind_max_bytes, {"sprite": 8388608, "portrait": 3145728, "background": 10485760,
                                               "mascot": 3145728})
        self.assertEqual(spec.max_bytes_for("sprite"), 8388608)
        self.assertEqual(spec.max_bytes_for("ui"), 512000)        # kind별 행이 없으면 공통 상한
        self.assertEqual(spec.root, "content/images")
        self.assertEqual(spec.manifest_path, "content/images/manifest.json")
        self.assertEqual(spec.sizes, {"portrait": (512, 512), "sprite": (512, 1280), "background": (1280, 720), "mascot": (512, 512),
                                      "place": (128, 128), "ui": (128, 128), "map": (1280, 720)})
        self.assertEqual(spec.ui_icon_ids, UI_IDS)

    def test_baseline_table_matches_vn(self):
        """6절 기준선 표의 px 값이 검사 코드(vn.py)와 같다."""
        sec = SPEC_TEXT.split("## 6.", 1)[1].split("\n## ", 1)[0]
        rows = dict(re.findall(r"^\| ([^|]+?) \| ([^|]+?) \| (?:자동|사람 검수) \|$", sec, re.M))
        self.assertIn(f"{vn.SPRITE_SIZE[0]}×{vn.SPRITE_SIZE[1]} RGBA", rows["sprite"])
        self.assertIn(f"알파 {vn.VISIBLE_ALPHA} 이상", rows["가로 가운데"])
        self.assertIn(f"{vn.SPRITE_SIZE[0] // 2}±{vn.CENTER_TOLERANCE}", rows["가로 가운데"])
        self.assertIn(f"{vn.TOP_MARGIN_MIN} 이상", rows["머리 위 여백"])
        self.assertIn("키 등급", rows["머리 꼭대기"])
        self.assertIn(f"y={vn.FOOT_Y}, 아래 여백 {vn.BOTTOM_MARGIN[0]}~{vn.BOTTOM_MARGIN[1]}", rows["발끝"])
        self.assertEqual(vn.SPRITE_SIZE[1] - vn.FOOT_Y, vn.BOTTOM_MARGIN[0] * 2)
        self.assertIn(f"{vn.BACKGROUND_SIZE[0]}×{vn.BACKGROUND_SIZE[1]}", rows["장면 배경"])

    def test_height_grade_table_matches_vn(self):
        """6절 키 등급 표의 값·인물 배정이 vn.HEIGHT_GRADES·CHARACTER_GRADE·face_box·portrait_top과 같다."""
        sec = SPEC_TEXT.split("## 6.", 1)[1].split("\n## ", 1)[0]
        rows = re.findall(r"^\| [^|`]+`(\w+)` \| (.+) \|$", sec, re.M)
        self.assertEqual({g for g, _ in rows}, set(vn.HEIGHT_GRADES))
        for name, rest in rows:
            cells = [c.strip() for c in rest.split(" | ")]
            g = vn.HEIGHT_GRADES[name]
            top, heads, head, eye, fx, fy, peye, ptop, people = cells
            self.assertEqual((int(top), float(heads), int(peye)), (g.top, g.heads, g.portrait_eye), name)
            self.assertAlmostEqual(float(head), g.head, delta=0.5)
            self.assertEqual(int(eye), round(g.eye_y), name)
            ids = re.findall(r"`(\w+)`", people)
            self.assertEqual(sorted(ids), sorted(c for c, v in vn.CHARACTER_GRADE.items() if v == name))
            x0, y0, x1, y1 = vn.face_box(ids[0])
            self.assertEqual((fx, fy), (f"{x0}~{x1 - 1}", f"{y0}~{y1 - 1}"), name)
            self.assertEqual(int(ptop), vn.portrait_top(ids[0]), name)

    def test_layout_table_matches_vn(self):
        """7절 배치 표의 평소·전신 컷 수치가 vn.py 모드와 같다."""
        sec = SPEC_TEXT.split("## 7.", 1)[1]
        x0, y0, x1, y1 = vn.place(vn.NORMAL)
        self.assertIn(f"0~{vn.NORMAL.rows[1]}행(머리~허리)을 부드럽게 0.8배 축소해 {x1 - x0}×{y1 - y0}, "
                      f"가로 가운데(x {x0}~{x1}), 위 y {y0}, 아랫변 y {y1}", sec)
        self.assertEqual(y1, vn.dialogue_box()[1])
        x0, y0, x1, y1 = vn.place(vn.FULL)
        self.assertIn(f"0.5배 축소해 {x1 - x0}×{y1 - y0}, 가로 가운데(x {x0}~{x1}), 위 y {y0}", sec)
        self.assertIn(f"무대 아래 {vn.DIALOGUE_HEIGHT}px", sec)

    def test_missing_section_fails(self):
        with self.assertRaises(SpecError):
            parse_spec_md(SPEC_TEXT.replace("## 3. UI 아이콘 목록", "## 3. 아이콘"))

    def test_bad_size_fails(self):
        with self.assertRaises(SpecError):
            parse_spec_md(SPEC_TEXT.replace("| 512×512 |", "| 512 |"))

    def test_duplicate_icon_fails(self):
        with self.assertRaises(SpecError):
            parse_spec_md(SPEC_TEXT.replace("| notebook |", "| vote |"))


class ExpectedEntriesTest(unittest.TestCase):
    def test_counts_by_kind_total_102(self):
        _, entries = _expected()
        by_kind: dict[str, int] = {}
        for e in entries:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        # 인물 13 + 표정 변형 24(9절 대상 8명 × 8절 표정 3)
        self.assertEqual(by_kind, {"portrait": 37, "sprite": 37, "background": 2, "mascot": 2,
                                   "place": 5, "ui": 10, "map": 1, "ad": 8})
        self.assertEqual(len(entries), 102)

    def test_portrait_derived_from_same_id_sprite(self):
        _, entries = _expected()
        portraits = {e.id: e for e in entries if e.kind == "portrait"}
        sprites = {e.id for e in entries if e.kind == "sprite"}
        self.assertEqual(set(portraits), sprites)
        for cid, e in portraits.items():
            self.assertEqual(e.derived_from, f"sprite:{cid}")
        self.assertEqual({e.id for e in entries if e.kind == "background"}, {"meeting", "night"})

    def test_mudang_variants_not_player_id(self):
        _, entries = _expected()
        for kind in ("portrait", "sprite"):
            ids = {e.id for e in entries if e.kind == kind}
            self.assertTrue({"mudang_f", "mudang_m"} <= ids)
            self.assertNotIn("mudang", ids)

    def test_ad_keys_use_campaign_slot_sizes_only_and_contract_path(self):
        _, entries = _expected()
        ads = {(e.id, e.size): e for e in entries if e.kind == "ad"}
        campaigns, _ = sources.load_campaigns()
        want: dict = {}
        for cp in campaigns:                       # 상태(paused 포함)와 무관하게 모든 소재
            for slot in cp["slots"]:
                for cr in cp["creatives"]:
                    want.setdefault((cr["creative_id"], tuple(SLOT_SIZES[AdSlot(slot)])),
                                    slot_image_path(cr["image_path"], AdSlot(slot)))
        self.assertEqual(set(ads), set(want))
        for key, url in want.items():
            self.assertEqual(ads[key].url, url)
            self.assertEqual(ads[key].path, "content/images/ad/" + url.rsplit("/", 1)[1])
            self.assertEqual((ads[key].width, ads[key].height), key[1])

    def test_non_ad_url_and_path_rule(self):
        _, entries = _expected()
        for e in entries:
            if e.kind != "ad":
                self.assertEqual(e.path, f"content/images/{e.kind}/{e.id}.png")
                self.assertEqual(e.url, f"/static/assets/{e.kind}/{e.id}.png")
                self.assertIsNone(e.size)

    def test_synthetic_campaign_skipped_and_same_size_slots_one_file(self):
        spec = parse_spec_md(SPEC_TEXT)
        cps = [{"campaign_id": "cp_x", "slots": ["lobby_banner", "result_banner"],
                "creatives": [{"creative_id": "cr_x", "image_path": "/static/ads/x.png"}]},
               {"campaign_id": "syn:cp_y", "slots": ["review_side"],
                "creatives": [{"creative_id": "cr_y", "image_path": "/static/ads/y.png"}]}]
        got = ad_entries(spec, cps, SLOT_SIZES_STR, slot_image_path)
        self.assertEqual([(e.id, e.size, e.url) for e in got],
                         [("cr_x", (728, 90), "/static/ads/x__728x90.png")])

    def test_ad_url_outside_static_ads_fails(self):
        spec = parse_spec_md(SPEC_TEXT)
        cps = [{"campaign_id": "cp_x", "slots": ["review_side"],
                "creatives": [{"creative_id": "cr_x", "image_path": "/media/x.png"}]}]
        with self.assertRaises(SpecError):
            ad_entries(spec, cps, SLOT_SIZES_STR, slot_image_path)

    def test_duplicate_id_fails(self):
        spec = parse_spec_md(SPEC_TEXT)
        with self.assertRaises(SpecError):
            expected_entries(spec, ["hunjang", "hunjang"], [], [], SLOT_SIZES_STR, slot_image_path)

    def test_fixture_slot_sizes_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as d:
            data = json.loads(sources.CAMPAIGNS_PATH.read_text(encoding="utf-8"))
            data["slot_sizes"]["review_side"] = [320, 250]
            p = Path(d) / "campaigns.json"
            p.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(SpecError):
                sources.load_campaigns(p)


class GenerateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_generate_counts_sizes_and_limit(self):
        r = run(self.tmp)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual(r.written, 102)
        self.assertEqual(sum(r.recon.files.values()), 102)
        self.assertEqual(sum(r.recon.manifest.values()), 102)
        for e in r.entries:
            data = (self.tmp / e.path).read_bytes()
            self.assertEqual(png.read_size(data), (e.width, e.height), e.path)
            self.assertLessEqual(len(data), 512000)
        m = result_metrics(r)
        self.assertEqual((m["expected_total"]["value"], m["manifest_total"]["value"],
                          m["files_total"]["value"]), (102, 102, 102))
        self.assertEqual((m["missing_count"]["value"], m["extra_count"]["value"]), (0, 0))

    def test_manifest_format_condition_2(self):
        run(self.tmp)
        items = json.loads((self.tmp / "content/images/manifest.json").read_text(encoding="utf-8"))
        keys = set()
        for d in items:
            want = ({"kind", "id", "path", "url", "placeholder"} | ({"size"} if d["kind"] == "ad" else set())
                    | ({"derived_from"} if d["kind"] == "portrait" else set())
                    | ({"edited_from"} if d["kind"] == "sprite" and "__" in d["id"] else set())
                    | ({"regions"} if d["kind"] == "map" else set()))
            self.assertEqual(set(d), want)
            self.assertIs(d["placeholder"], True)
            keys.add((d["kind"], d["id"], tuple(d.get("size") or ())))
        self.assertEqual(len(keys), len(items))

    def test_deterministic_bytes(self):
        run(self.tmp)
        first = {p: p.read_bytes() for p in (self.tmp / "content/images").rglob("*") if p.is_file()}
        run(self.tmp)
        second = {p: p.read_bytes() for p in (self.tmp / "content/images").rglob("*") if p.is_file()}
        self.assertEqual(first, second)

    def test_other_zlib_bytes_kept(self):
        """압축 바이트만 다른 임시 PNG(zlib-ng 등 흉내: 압축 수준 6)는 같은 그림으로 보고 다시 쓰지 않는다. 대조·검사도 통과."""
        run(self.tmp)
        paths = [self.tmp / "content/images" / rel for rel in
                 ("sprite/jumo.png", "portrait/jumo.png", "sprite/jumo__joy.png", "mascot/kkachi.png", "ui/vote.png",
                  "background/village_day.png")]
        paths = [x for x in paths if x.is_file()]
        self.assertGreaterEqual(len(paths), 4)
        other = {}
        for x in paths:
            old = x.read_bytes()
            other[x] = _recompress(old, 6)
            self.assertNotEqual(other[x], old, x.name)
            self.assertTrue(png.same_image(other[x], old), x.name)
            x.write_bytes(other[x])
        r = run(self.tmp)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual(r.protected_files, [])
        for x in paths:
            self.assertEqual(x.read_bytes(), other[x], x.name)        # 파일 바이트(SHA) 보존
        self.assertEqual(_quiet_main(["--repo", str(self.tmp), "--check"]), 0)

    def test_same_pixels_over_limit_rewritten(self):
        """픽셀이 같아도 용량 상한을 넘는 임시 PNG는 다시 쓴다(상한은 파일 바이트 기준)."""
        run(self.tmp)
        x = self.tmp / "content/images/ui/vote.png"
        fresh = x.read_bytes()
        limit = sources.load_spec().max_bytes_for("ui")
        pad = png._chunk(b"tEXt", b"pad\x00" + b"x" * (limit + 1))
        bloated = fresh[:-12] + pad + fresh[-12:]
        self.assertTrue(png.same_image(bloated, fresh))
        x.write_bytes(bloated)
        r = run(self.tmp)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual(x.read_bytes(), fresh)

    def test_same_image_pixels(self):
        a = png.encode_rgb(4, 2, [bytes(range(12)), bytes(12)])
        self.assertTrue(png.same_image(a, _recompress(a, 1)))
        self.assertTrue(png.same_image(a, png.encode_rgb(4, 2, [bytes(range(12)), bytes(12)], optimize=True)))
        self.assertFalse(png.same_image(a, png.encode_rgb(4, 2, [bytes(range(12)), bytes(11) + b""])))
        self.assertFalse(png.same_image(a, png.encode_rgba(4, 2, [bytes(16), bytes(16)])))
        self.assertFalse(png.same_image(a, png.encode_rgb(2, 4, [bytes(6)] * 4)))
        self.assertFalse(png.same_image(a, b"not png"))
        trns = a[:33] + png._chunk(b"tRNS", bytes(6)) + a[33:]            # 투명색 지정: 픽셀 해석이 달라짐
        self.assertFalse(png.same_image(trns, a))
        broken = bytearray(_recompress(a, 1))
        broken[-5] ^= 1                                                  # IEND CRC 손상
        self.assertFalse(png.same_image(bytes(broken), a))
        self.assertFalse(png.same_image(a[:-12], a))                     # IEND 없음

    def test_real_file_is_kept(self):
        run(self.tmp)
        mpath = self.tmp / "content/images/manifest.json"
        items = json.loads(mpath.read_text(encoding="utf-8"))
        target = next(d for d in items if d["kind"] == "map")
        target["placeholder"] = False
        mpath.write_text(json.dumps(items), encoding="utf-8")
        real = png.encode_rgb(1280, 720, art.render("map", "other", 1280, 720).rows)
        (self.tmp / target["path"]).write_bytes(real)
        r = run(self.tmp)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual((r.written, r.kept_real), (101, 1))
        self.assertEqual((self.tmp / target["path"]).read_bytes(), real)
        after = json.loads(mpath.read_text(encoding="utf-8"))
        self.assertIs(next(d for d in after if d["kind"] == "map")["placeholder"], False)

    def test_missing_extra_and_wrong_size_detected(self):
        run(self.tmp)
        (self.tmp / "content/images/ui/vote.png").unlink()
        (self.tmp / "content/images/ui/stray.png").write_bytes(b"x")
        (self.tmp / "content/images/place/well.png").write_bytes(
            png.encode_rgb(64, 64, art.render("place", "well", 64, 64).rows))
        r = run(self.tmp, write=False)
        self.assertFalse(r.ok)
        self.assertEqual(r.recon.missing_files, ["content/images/ui/vote.png"])
        self.assertEqual(r.recon.extra_files, ["content/images/ui/stray.png"])
        self.assertEqual(len(r.bad_files), 1)
        self.assertIn("well.png", r.bad_files[0])

    def test_cli_exit_codes_and_log(self):
        self.assertEqual(_quiet_main(["--repo", str(self.tmp), "--check"]), 1)
        self.assertEqual(_quiet_main(["--repo", str(self.tmp)]), 0)
        lines = (self.tmp / "out/logs/assets.jsonl").read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[-1])
        for k in ("ts", "level", "service", "component", "msg"):
            self.assertIn(k, rec)

    def test_result_json_format(self):
        self.assertEqual(_quiet_main(["--repo", str(self.tmp), "--result"]), 0)
        body = json.loads((self.tmp / "docs/evidence/perf/E-05-05/placeholders.json").read_text(encoding="utf-8"))
        self.assertEqual(body["task"], "E-05-05")
        self.assertEqual(body["metrics"]["expected_total"], {"value": 102, "unit": "개"})


class CodexFindingsTest(unittest.TestCase):
    """Codex 리뷰 지적 재현: 빠진 ID의 실제 파일 보호, manifest path/url 불일치, 깨진 PNG."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_real_entry_survives_id_removal_and_restore(self):
        run(self.tmp)
        mpath = self.tmp / "content/images/manifest.json"
        items = json.loads(mpath.read_text(encoding="utf-8"))
        target = next(d for d in items if d["kind"] == "sprite" and d["id"] == "boy")
        target["placeholder"] = False
        mpath.write_text(json.dumps(items), encoding="utf-8")
        real = png.encode_rgba(512, 1280, portrait_art.render_sprite("jumo", 512, 1280))
        (self.tmp / target["path"]).write_bytes(real)
        self.assertTrue(run(self.tmp).ok)                  # 초상이 실제 sprite에서 파생되어 쌍이 모두 실제

        bundle = json.loads(sources.BUNDLE_PATH.read_text(encoding="utf-8"))
        bundle["characters"] = [c for c in bundle["characters"] if c["npc_id"] != "boy"]
        removed = self.tmp / "bundle_removed.json"
        removed.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        r = run(self.tmp, bundle_path=removed)            # ID 제거: 실제 항목은 남고 extra로 보고
        self.assertFalse(r.ok)
        self.assertEqual(r.recon.extra_in_manifest, ["portrait:boy", "sprite:boy"])

        r = run(self.tmp)                                  # ID 복원: 실제 파일을 덮어쓰지 않는다
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual(r.kept_real, 1)                   # sprite. 초상은 그 sprite에서 다시 파생(같은 바이트)
        self.assertEqual((self.tmp / target["path"]).read_bytes(), real)

    def test_unmarked_real_file_not_overwritten_without_force(self):
        """실제 파일을 넣고 placeholder 표시를 안 바꿔도 다시 실행이 덮어쓰지 않는다(--force일 때만)."""
        run(self.tmp)
        path = self.tmp / "content/images/portrait/jumo.png"
        real = png.encode_rgb(512, 512, art.render("map", "real", 512, 512).rows)
        path.write_bytes(real)
        r = run(self.tmp)
        self.assertFalse(r.ok)
        self.assertEqual(r.protected_files, ["content/images/portrait/jumo.png"])
        self.assertEqual(path.read_bytes(), real)
        self.assertEqual(_quiet_main(["--repo", str(self.tmp)]), 1)
        self.assertEqual(path.read_bytes(), real)
        self.assertEqual(_quiet_main(["--repo", str(self.tmp), "--force"]), 0)
        self.assertNotEqual(path.read_bytes(), real)

    def test_manifest_path_mismatch_detected(self):
        run(self.tmp)
        mpath = self.tmp / "content/images/manifest.json"
        items = json.loads(mpath.read_text(encoding="utf-8"))
        next(d for d in items if d["id"] == "vote")["url"] = "/static/assets/ui/votes.png"
        mpath.write_text(json.dumps(items), encoding="utf-8")
        r = run(self.tmp, write=False)
        self.assertFalse(r.ok)
        self.assertEqual(len(r.recon.manifest_mismatch), 1)
        self.assertIn("ui:vote", r.recon.manifest_mismatch[0])

    def test_truncated_or_corrupt_png_rejected(self):
        good = png.encode_rgb(4, 4, art.render("ui", "x", 4, 4).rows)
        self.assertEqual(png.read_size(good), (4, 4))
        for bad in (good[:24], good[:-12], good[:-1], good[:23] + bytes([good[23] ^ 0xFF]) + good[24:]):
            with self.assertRaises(ValueError):
                png.read_size(bad)

    def test_interlaced_png_data_length_checked(self):
        import struct
        import zlib

        def interlaced(w, h, raw_len):
            ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 1)
            return (png.SIGNATURE + png._chunk(b"IHDR", ihdr)
                    + png._chunk(b"IDAT", zlib.compress(b"\x00" * raw_len)) + png._chunk(b"IEND", b""))

        need = png.expected_data_length(7, 3, 24, True)
        # Adam7 손계산(7×3, 3바이트/픽셀): 단계별 (가로, 세로) = (1,1) (1,1) 없음 (2,1) (4,1) (3,2) (7,1)
        self.assertEqual(need, 4 + 4 + 0 + 7 + 13 + 20 + 22)
        self.assertEqual(png.read_size(interlaced(7, 3, need)), (7, 3))
        for bad_len in (0, need - 1, need + 1):
            with self.assertRaises(ValueError):
                png.read_size(interlaced(7, 3, bad_len))

    def test_corrupt_file_reported_as_bad(self):
        run(self.tmp)
        p = self.tmp / "content/images/ui/vote.png"
        p.write_bytes(p.read_bytes()[:30])
        r = run(self.tmp, write=False)
        self.assertFalse(r.ok)
        self.assertTrue(any("vote.png" in b for b in r.bad_files))


class ReconcileTest(unittest.TestCase):
    def test_extra_manifest_key(self):
        e = AssetEntry("ui", "vote", "r/ui/vote.png", "/static/assets/ui/vote.png", 128, 128)
        x = AssetEntry("ui", "old", "r/ui/old.png", "/static/assets/ui/old.png", 128, 128)
        rc = reconcile([e], [e, x], ["r/ui/vote.png"], "r")
        self.assertEqual(rc.extra_in_manifest, ["ui:old"])
        self.assertFalse(rc.ok)


class CommittedSetTest(unittest.TestCase):
    def test_repository_set_matches_spec(self):
        """저장소에 커밋된 세트가 규격표·픽스처와 맞는다(쓰지 않고 대조만)."""
        r = run(ROOT, write=False)
        self.assertTrue(r.ok, format_report(r))


class ArtTest(unittest.TestCase):
    def test_png_roundtrip_and_non_png(self):
        data = png.encode_rgb(3, 2, art.render("ui", "vote", 3, 2).rows)
        self.assertEqual(png.read_size(data), (3, 2))
        with self.assertRaises(ValueError):
            png.read_size(b"GIF89a....................")

    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            art.render("mascot", "x", 10, 10)


if __name__ == "__main__":
    unittest.main()
