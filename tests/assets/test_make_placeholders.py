"""E-05-05 임시 에셋: 규격표 해석, 기대 항목(37), 생성·대조, 실제 파일 보존."""
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
from tools.assets import art, png, sources
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


def _expected():
    spec = parse_spec_md(SPEC_TEXT)
    campaigns, slot_sizes = sources.load_campaigns()
    return spec, expected_entries(spec, sources.load_npc_ids(), sources.load_place_ids(),
                                  campaigns, slot_sizes, sources.slot_image_path)


class SpecTableTest(unittest.TestCase):
    def test_values_match_task_condition_1(self):
        spec = parse_spec_md(SPEC_TEXT)
        self.assertEqual(spec.image_format, "png")
        self.assertEqual(spec.max_bytes, 512000)
        self.assertEqual(spec.root, "content/images")
        self.assertEqual(spec.manifest_path, "content/images/manifest.json")
        self.assertEqual(spec.sizes, {"portrait": (512, 512), "place": (128, 128),
                                      "ui": (128, 128), "map": (1280, 720)})
        self.assertEqual(spec.ui_icon_ids, UI_IDS)

    def test_dot_grid_table_condition_1(self):
        """6절 도트 격자: 전 유형 ×4, 초상 128×128, 아이콘 32×32, 지도 320×180, 광고 182×22·75×62 + 위아래 1px."""
        sec = SPEC_TEXT.split("## 6.", 1)[1]
        pat = r"^\| ([a-z]+(?: \d+×\d+)?) \| (\d+)×(\d+) \| ×(\d+) \| (\d+)×(\d+) \|"
        rows = {m[0]: tuple(int(v) for v in m[1:]) for m in re.findall(pat, sec, re.M)}
        self.assertEqual(rows, {
            "portrait": (128, 128, 4, 512, 512), "place": (32, 32, 4, 128, 128), "ui": (32, 32, 4, 128, 128),
            "map": (320, 180, 4, 1280, 720), "ad 728×90": (182, 22, 4, 728, 90), "ad 300×250": (75, 62, 4, 300, 250)})
        spec = parse_spec_md(SPEC_TEXT)
        for kind, (gw, gh, k, fw, fh) in rows.items():
            self.assertEqual(gw * k, fw, kind)
            if kind.startswith("ad"):
                self.assertEqual(fh - gh * k, 2, kind)      # 위아래 1px씩 배경색
            else:
                self.assertEqual(gh * k, fh, kind)
                self.assertEqual(spec.sizes[kind], (fw, fh), kind)

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
    def test_counts_by_kind_total_37(self):
        _, entries = _expected()
        by_kind: dict[str, int] = {}
        for e in entries:
            by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
        self.assertEqual(by_kind, {"portrait": 13, "place": 5, "ui": 10, "map": 1, "ad": 8})
        self.assertEqual(len(entries), 37)

    def test_mudang_variants_not_player_id(self):
        _, entries = _expected()
        ids = {e.id for e in entries if e.kind == "portrait"}
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
               {"campaign_id": "syn:cp_y", "slots": ["llm_wait"],
                "creatives": [{"creative_id": "cr_y", "image_path": "/static/ads/y.png"}]}]
        got = ad_entries(spec, cps, SLOT_SIZES_STR, slot_image_path)
        self.assertEqual([(e.id, e.size, e.url) for e in got],
                         [("cr_x", (728, 90), "/static/ads/x__728x90.png")])

    def test_ad_url_outside_static_ads_fails(self):
        spec = parse_spec_md(SPEC_TEXT)
        cps = [{"campaign_id": "cp_x", "slots": ["llm_wait"],
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
            data["slot_sizes"]["llm_wait"] = [320, 250]
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
        self.assertEqual(r.written, 37)
        self.assertEqual(sum(r.recon.files.values()), 37)
        self.assertEqual(sum(r.recon.manifest.values()), 37)
        for e in r.entries:
            data = (self.tmp / e.path).read_bytes()
            self.assertEqual(png.read_size(data), (e.width, e.height), e.path)
            self.assertLessEqual(len(data), 512000)
        m = result_metrics(r)
        self.assertEqual((m["expected_total"]["value"], m["manifest_total"]["value"],
                          m["files_total"]["value"]), (37, 37, 37))
        self.assertEqual((m["missing_count"]["value"], m["extra_count"]["value"]), (0, 0))

    def test_manifest_format_condition_2(self):
        run(self.tmp)
        items = json.loads((self.tmp / "content/images/manifest.json").read_text(encoding="utf-8"))
        keys = set()
        for d in items:
            want = {"kind", "id", "path", "url", "placeholder"} | ({"size"} if d["kind"] == "ad" else set())
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

    def test_real_file_is_kept(self):
        run(self.tmp)
        mpath = self.tmp / "content/images/manifest.json"
        items = json.loads(mpath.read_text(encoding="utf-8"))
        target = next(d for d in items if d["id"] == "hunjang")
        target["placeholder"] = False
        mpath.write_text(json.dumps(items), encoding="utf-8")
        real = png.encode_rgb(512, 512, art.render("map", "other", 512, 512).rows)
        (self.tmp / target["path"]).write_bytes(real)
        r = run(self.tmp)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual((r.written, r.kept_real), (36, 1))
        self.assertEqual((self.tmp / target["path"]).read_bytes(), real)
        after = json.loads(mpath.read_text(encoding="utf-8"))
        self.assertIs(next(d for d in after if d["id"] == "hunjang")["placeholder"], False)

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
        self.assertEqual(body["metrics"]["expected_total"], {"value": 37, "unit": "개"})


class CodexFindingsTest(unittest.TestCase):
    """Codex 리뷰 지적 재현: 빠진 ID의 실제 파일 보호, manifest path/url 불일치, 깨진 PNG."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_real_entry_survives_id_removal_and_restore(self):
        run(self.tmp)
        mpath = self.tmp / "content/images/manifest.json"
        items = json.loads(mpath.read_text(encoding="utf-8"))
        target = next(d for d in items if d["id"] == "boy")
        target["placeholder"] = False
        mpath.write_text(json.dumps(items), encoding="utf-8")
        real = png.encode_rgb(512, 512, art.render("map", "real", 512, 512).rows)
        (self.tmp / target["path"]).write_bytes(real)

        bundle = json.loads(sources.BUNDLE_PATH.read_text(encoding="utf-8"))
        bundle["characters"] = [c for c in bundle["characters"] if c["npc_id"] != "boy"]
        removed = self.tmp / "bundle_removed.json"
        removed.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        r = run(self.tmp, bundle_path=removed)            # ID 제거: 실제 항목은 남고 extra로 보고
        self.assertFalse(r.ok)
        self.assertEqual(r.recon.extra_in_manifest, ["portrait:boy"])

        r = run(self.tmp)                                  # ID 복원: 실제 파일을 덮어쓰지 않는다
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual(r.kept_real, 1)
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
