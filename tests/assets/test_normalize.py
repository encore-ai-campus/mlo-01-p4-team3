"""A-09-05: 투명 원본 정규화(마스코트 배치·머리카락 윗선 표·흐름)."""
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


if __name__ == "__main__":
    unittest.main()
