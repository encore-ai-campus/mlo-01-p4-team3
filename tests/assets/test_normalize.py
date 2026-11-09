"""A-09-05: 투명 원본 정규화(마스코트 배치·머리카락 윗선 표·흐름). A-10-05: 장면 배경 정규화."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.assets import normalize, png, sources, vn


def _rgba(w: int, h: int, box: tuple[int, int, int, int]) -> list[bytearray]:
    x0, y0, x1, y1 = box
    rows = [bytearray(w * 4) for _ in range(h)]
    for y in range(y0, y1):
        for x in range(x0, x1):
            rows[y][x * 4:x * 4 + 4] = bytes((120, 80, 40, 255))
    return rows


class FitMascotTest(unittest.TestCase):
    def test_wide_mascot_is_centered_on_foot_line_with_one_scale(self):
        out = normalize.fit_mascot(200, _rgba(200, 100, (0, 10, 200, 90)))   # 200×80 상자
        self.assertEqual(vn.check_mascot(512, 512, 4, out), [])
        x0, y0, x1, y1 = vn.visible_box(512, out)
        self.assertEqual(y1, normalize.MASCOT_FOOT_Y)
        self.assertEqual((x1 - x0, y1 - y0), (200, 80))          # 확대하지 않음

    def test_large_mascot_shrinks_keeping_ratio_and_margins(self):
        out = normalize.fit_mascot(1000, _rgba(1000, 1200, (100, 0, 900, 1200)))  # 800×1200
        x0, y0, x1, y1 = vn.visible_box(512, out)
        self.assertEqual(y1, normalize.MASCOT_FOOT_Y)
        self.assertEqual(y0, normalize.MASCOT_TOP)
        self.assertAlmostEqual((x1 - x0) / (y1 - y0), 800 / 1200, delta=0.01)
        self.assertEqual(vn.check_mascot(512, 512, 4, out), [])

    def test_empty_or_bad_rows_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize.fit_mascot(4, [bytearray(16)])
        with self.assertRaises(ValueError):
            normalize.fit_mascot(4, [bytearray(15)])


class HairTopTableTest(unittest.TestCase):
    def test_every_first_play_character_has_grade_and_reason(self):
        for cid, (y, reason) in normalize.HAIR_TOP.items():
            with self.subTest(cid=cid):
                vn.grade_for(cid)
                self.assertGreater(y, 0)
                self.assertTrue(reason)


class NormalizeFlowTest(unittest.TestCase):
    def test_writes_files_and_flips_only_target_entries(self):
        spec = sources.load_spec()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, src = root / "repo", root / "src"
            (repo / spec.manifest_path).parent.mkdir(parents=True)
            entries = json.loads((sources.ROOT / spec.manifest_path).read_text(encoding="utf-8"))
            targets = {f"sprite:{c}" for c in normalize.HAIR_TOP}
            for d in entries:               # 대상 인물의 초상만 실제 표시(이미 한 번 맞춘 뒤 다시 돌리는 경우)
                d["placeholder"] = not (d["kind"] == "portrait" and d.get("derived_from") in targets)
            (repo / spec.manifest_path).write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
            (src / "characters").mkdir(parents=True)
            (src / "mascots").mkdir()
            for cid, (top, _) in normalize.HAIR_TOP.items():   # 머리카락 윗선부터 발끝까지 채운 작은 원본
                rows = _rgba(40, 120, (5, top // 20, 35, 110))
                (src / "characters" / f"{cid}.png").write_bytes(png.encode_rgba(40, 120, rows))
            small = {cid: (top // 20, "test") for cid, (top, _) in normalize.HAIR_TOP.items()}
            for name in normalize.MASCOT_SOURCES.values():
                (src / "mascots" / name).write_bytes(png.encode_rgba(60, 60, _rgba(60, 60, (10, 5, 50, 55))))
            with mock.patch.dict(normalize.HAIR_TOP, small):
                report = normalize.normalize(src, repo)
            self.assertEqual(len(report), len(normalize.HAIR_TOP) + len(normalize.MASCOT_SOURCES))
            data = json.loads((repo / spec.manifest_path).read_text(encoding="utf-8"))
            real = {(d["kind"], d["id"]) for d in data if not d["placeholder"]}
            want = {("sprite", c) for c in normalize.HAIR_TOP} | {("mascot", m) for m in normalize.MASCOT_SOURCES}
            self.assertEqual(real, want)          # 다시 맞춘 sprite의 초상은 재파생 대상(임시)으로 돌아간다
            derived = {d["derived_from"] for d in data if d["kind"] == "portrait" and not d["placeholder"]}
            self.assertFalse(derived & {f"sprite:{c}" for c in normalize.HAIR_TOP})
            for key, m in report.items():
                with self.subTest(key=key):
                    kind = key.split(":")[0]
                    self.assertEqual(m["foot_y"], vn.FOOT_Y if kind == "sprite" else normalize.MASCOT_FOOT_Y)
                    self.assertTrue((repo / f"{spec.root}/{kind}/{key.split(':')[1]}.png").is_file())

    def test_failure_leaves_repo_untouched(self):
        spec = sources.load_spec()
        with tempfile.TemporaryDirectory() as temp:
            repo, src = Path(temp) / "repo", Path(temp) / "src"
            (repo / spec.manifest_path).parent.mkdir(parents=True)
            shutil.copy(sources.ROOT / spec.manifest_path, repo / spec.manifest_path)
            before = (repo / spec.manifest_path).read_bytes()
            (src / "characters").mkdir(parents=True)
            with self.assertRaises(OSError):
                normalize.normalize(src, repo)            # 원본 없음
            self.assertEqual((repo / spec.manifest_path).read_bytes(), before)
            self.assertFalse((repo / spec.root / "sprite").exists())


def _rgb(w: int, h: int, color=(90, 110, 130)) -> list[bytearray]:
    return [bytearray(bytes(color) * w) for _ in range(h)]


class BackgroundTest(unittest.TestCase):
    def test_crop_is_exact_16_9_centered(self):
        self.assertEqual(normalize.background_crop(1672, 941), (4, 2, 1668, 938))
        self.assertEqual(normalize.background_crop(1280, 720), (0, 0, 1280, 720))
        x0, y0, x1, y1 = normalize.background_crop(2000, 1500)
        self.assertEqual(((x1 - x0) * 9, y0, 1500 - y1), ((y1 - y0) * 16, 187, 188))

    def test_small_source_is_rejected_without_upscale(self):
        with self.assertRaises(ValueError):
            normalize.background_crop(1279, 720)

    def test_fit_background_gives_opaque_rgb_stage(self):
        rows = _rgb(1312, 729)                  # 자르기 상자 1296×729, 왼쪽 x 8부터
        for x in range(8):                      # 자르기 상자 밖 왼쪽 띠는 결과에 남지 않는다
            for r in rows:
                r[x * 3:x * 3 + 3] = bytes((255, 0, 0))
        out = normalize.fit_background(1312, 729, 3, rows)
        self.assertEqual((len(out[0]) // 3, len(out)), vn.BACKGROUND_SIZE)
        self.assertEqual(vn.check_background(*vn.BACKGROUND_SIZE, 3, out), [])
        self.assertEqual(bytes(out[360][0:3]), bytes((90, 110, 130)))

    def test_rgba_with_transparency_is_rejected(self):
        rows = [bytearray(bytes((1, 2, 3, 255)) * 1280) for _ in range(720)]
        rows[5][3] = 0
        with self.assertRaises(ValueError):
            normalize.fit_background(1280, 720, 4, rows)

    def test_flow_writes_backgrounds_and_flips_only_them(self):
        spec = sources.load_spec()
        with tempfile.TemporaryDirectory() as temp:
            repo, src = Path(temp) / "repo", Path(temp) / "src"
            (repo / spec.manifest_path).parent.mkdir(parents=True)
            shutil.copy(sources.ROOT / spec.manifest_path, repo / spec.manifest_path)
            before = {(d["kind"], d["id"], str(d.get("size"))): d["placeholder"]
                      for d in json.loads((repo / spec.manifest_path).read_text(encoding="utf-8"))}
            src.mkdir()
            paths = {}
            for bid in ("meeting", "night"):
                paths[bid] = src / f"{bid}.png"
                paths[bid].write_bytes(png.encode_rgb(1296, 729, _rgb(1296, 729)))
            report = normalize.normalize_backgrounds(paths, repo)
            again = normalize.fit_background(1296, 729, 3, _rgb(1296, 729))
            self.assertEqual(set(report), {"background:meeting", "background:night"})
            data = json.loads((repo / spec.manifest_path).read_text(encoding="utf-8"))
            for d in data:
                key = (d["kind"], d["id"], str(d.get("size")))
                with self.subTest(key=key):
                    want = False if d["kind"] == "background" else before[key]
                    self.assertEqual(d["placeholder"], want)
            w, h, ch, rows = png.decode((repo / spec.root / "background" / "meeting.png").read_bytes())
            self.assertEqual((w, h, ch), (*vn.BACKGROUND_SIZE, 3))
            self.assertEqual(rows, [bytes(r) for r in again])      # 같은 입력이면 같은 픽셀

    def test_cli_parses_background_pairs(self):
        self.assertEqual(normalize._parse_backgrounds(["meeting=a.png"]), {"meeting": Path("a.png")})
        for bad in (["meeting"], ["=a.png"], ["meeting=a.png", "meeting=b.png"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                normalize._parse_backgrounds(bad)


if __name__ == "__main__":
    unittest.main()
