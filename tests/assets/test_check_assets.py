"""E-08-07 에셋 정식 검사: 에셋2(생성 기록)·에셋3(규격)·에셋4(1:1), 임시 허용 모드, 지도 장소 좌표."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.assets import audit, png, sources
from tools.assets.check_assets import check, format_report, main, result_metrics
from tools.assets.make_placeholders import run
from tools.assets.spec import AssetEntry, SpecError, map_regions

ROOT = sources.ROOT
MANIFEST = "content/images/manifest.json"
SOURCES_MD = "content/images/SOURCES.md"
HEADER = "Codex 내장 image_gen · 비상업 교육 프로젝트용 생성물\n\n"
TABLE = ("| 결과 파일 | 도구 | 모델·버전 | 프롬프트 전문 | 부정 프롬프트 | 시드 | 생성일 |\n"
         "|---|---|---|---|---|---|---|\n")


def _row(path: str, seed: str = "42") -> str:
    return f"| `{path}` | image_gen | v1 | 마을 회의 장면 | 글자 없음 | {seed} | 2026-11-08 |\n"


def _quiet(argv):
    with contextlib.redirect_stdout(io.StringIO()):
        return main(argv)


class _Repo(unittest.TestCase):
    """임시 세트를 한 번 만들어 두고 테스트마다 복사해 고친다."""
    template: Path

    @classmethod
    def setUpClass(cls):
        cls.template = Path(tempfile.mkdtemp())
        with contextlib.redirect_stdout(io.StringIO()):
            r = run(cls.template)
        assert r.ok

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.template, True)

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.repo, True)
        shutil.copytree(self.template / "content", self.repo / "content")

    def edit_manifest(self, fn):
        p = self.repo / MANIFEST
        items = json.loads(p.read_text(encoding="utf-8"))
        fn(items)
        p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def mark_real(self, kind: str, cid: str):
        def fn(items):
            for d in items:
                if d["kind"] == kind and d["id"] == cid:
                    d["placeholder"] = False
        self.edit_manifest(fn)

    def write_sources(self, body: str):
        (self.repo / SOURCES_MD).write_text(body, encoding="utf-8")


class PlaceholderSetTest(_Repo):
    def test_generated_placeholder_set_passes(self):
        r = check(self.repo, placeholder_mode=True)
        self.assertTrue(r.ok, format_report(r))
        self.assertEqual((r.real_count, r.placeholder_count), (0, 102))
        m = result_metrics(r)
        self.assertEqual((m["missing_count"]["value"], m["extra_count"]["value"]), (0, 0))

    def test_default_mode_rejects_placeholders(self):
        r = check(self.repo)
        self.assertFalse(r.ok)
        self.assertEqual(len(r.policy_errors), 102)

    def test_allow_placeholder_only_listed_kinds(self):
        r = check(self.repo, allowed=frozenset({"place", "ui", "map", "ad"}))
        bad_kinds = {e.split(":")[0] for e in r.policy_errors}
        self.assertEqual(bad_kinds, {"portrait", "sprite", "background", "mascot"})
        self.assertFalse(r.ok)


class Asset4Test(_Repo):
    def test_missing_file(self):
        (self.repo / "content/images/ui/vote.png").unlink()
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.recon.missing_files, ["content/images/ui/vote.png"])
        self.assertFalse(r.ok)

    def test_extra_file(self):
        shutil.copy(self.repo / "content/images/ui/vote.png", self.repo / "content/images/ui/old_icon.png")
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.recon.extra_files, ["content/images/ui/old_icon.png"])
        self.assertFalse(r.ok)

    def test_missing_manifest_entry(self):
        self.edit_manifest(lambda items: items.remove(next(d for d in items if d["id"] == "notebook")))
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.recon.missing_in_manifest, ["ui:notebook"])
        self.assertFalse(r.ok)


class Asset3Test(_Repo):
    def test_wrong_size(self):
        (self.repo / "content/images/ui/vote.png").write_bytes(png.encode_rgb(64, 64, [bytes(64 * 3)] * 64))
        r = check(self.repo, placeholder_mode=True)
        self.assertTrue(any("vote.png: 크기 64x64" in b for b in r.bad_files), r.bad_files)

    def test_over_byte_limit(self):
        p = self.repo / "content/images/place/well.png"
        data = p.read_bytes()        # IEND 앞에 보조 청크를 넣어 PNG 구조는 그대로 두고 용량만 키운다
        p.write_bytes(data[:-12] + png._chunk(b"zzTx", b"\0" * 600_000) + data[-12:])
        r = check(self.repo, placeholder_mode=True)
        self.assertTrue(any("well.png" in b and "> 512000" in b for b in r.bad_files), r.bad_files)

    def test_not_png(self):
        (self.repo / "content/images/ui/vote.png").write_bytes(b"GIF89a" + b"\0" * 40)
        r = check(self.repo, placeholder_mode=True)
        self.assertTrue(any("vote.png: PNG 아님" in b for b in r.bad_files), r.bad_files)

    def test_wrong_path_name(self):
        def fn(items):
            d = next(d for d in items if d["kind"] == "ui" and d["id"] == "vote")
            d["path"] = "content/images/ui/Vote.png"
        self.edit_manifest(fn)
        r = check(self.repo, placeholder_mode=True)
        self.assertTrue(any(m.startswith("ui:vote: path/url") for m in r.recon.manifest_mismatch))
        self.assertFalse(r.ok)


class Asset2Test(_Repo):
    def test_real_file_without_record(self):
        self.mark_real("background", "night")
        self.write_sources(HEADER + TABLE + _row("content/images/background/meeting.png"))
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.record_errors, ["content/images/background/night.png: 생성 기록 행 없음"])
        self.assertFalse(r.ok)

    def test_no_sources_file(self):
        self.mark_real("background", "night")
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(len(r.record_errors), 1)
        self.assertIn("생성 기록 문서 없음", r.record_errors[0])

    def test_record_with_all_fields_passes(self):
        self.mark_real("background", "night")
        self.write_sources(HEADER + TABLE + _row("content/images/background/night.png"))
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.record_errors, [])
        self.assertTrue(r.ok, format_report(r))

    def test_empty_field_reported(self):
        self.mark_real("background", "night")
        self.write_sources(HEADER + TABLE + _row("content/images/background/night.png", seed="-"))
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.record_errors, ["content/images/background/night.png: 생성 기록 칸 비었음(시드)"])

    def test_disclaimer_required(self):
        self.mark_real("background", "night")
        self.write_sources("도구 메모\n\n" + TABLE + _row("content/images/background/night.png"))
        r = check(self.repo, placeholder_mode=True)
        self.assertTrue(any("머리말" in e for e in r.record_errors), r.record_errors)

    def test_disclaimer_needs_tool_name(self):
        """머리말에 문구만 있고 도구 이름이 없으면 위반(Codex 리뷰)."""
        e = AssetEntry("map", "village", "content/images/map/village.png", "u", 1280, 720, placeholder=False)
        body = TABLE + _row(e.path)
        bare = audit.check_records([e], audit.parse_sources_md("# 비상업 교육 프로젝트용 생성물\n\n" + body, "content/images"))
        self.assertTrue(any("머리말" in m for m in bare), bare)
        self.assertEqual(audit.check_records([e], audit.parse_sources_md(HEADER + body, "content/images")), [])

    def test_negative_column_is_not_prompt(self):
        """"부정 프롬프트" 칸만 있고 프롬프트 전문 칸이 없으면 프롬프트 전문 누락(Codex 리뷰)."""
        table = ("| 결과 파일 | 도구 | 모델·버전 | 부정 프롬프트 | 시드 | 생성일 |\n|---|---|---|---|---|---|\n"
                 "| `content/images/map/village.png` | image_gen | v1 | 글자 없음 | 42 | 2026-11-08 |\n")
        e = AssetEntry("map", "village", "content/images/map/village.png", "u", 1280, 720, placeholder=False)
        self.assertEqual(audit.check_records([e], audit.parse_sources_md(HEADER + table, "content/images")),
                         ["content/images/map/village.png: 생성 기록 칸 비었음(프롬프트 전문)"])

    def test_placeholder_mode_still_checks_real_files(self):
        """`--placeholder`(전체 임시 허용)도 실제 파일의 에셋2 검사를 건너뛰지 않는다(7번)."""
        self.mark_real("background", "night")
        self.write_sources(HEADER + TABLE)
        for kwargs in ({"placeholder_mode": True}, {"allowed": frozenset({"place", "ui", "map", "ad"})}, {}):
            r = check(self.repo, **kwargs)
            self.assertIn("content/images/background/night.png: 생성 기록 행 없음", r.record_errors, kwargs)

    def test_derived_portrait_uses_sprite_record(self):
        entries = [
            AssetEntry("sprite", "hunjang", "content/images/sprite/hunjang.png", "u", 512, 1280, placeholder=False),
            AssetEntry("portrait", "hunjang", "content/images/portrait/hunjang.png", "u", 512, 512,
                       placeholder=False, derived_from="sprite:hunjang"),
        ]
        idx = audit.parse_sources_md(HEADER + TABLE + _row("content/images/sprite/hunjang.png"), "content/images")
        self.assertEqual(audit.check_records(entries, idx), [])
        idx = audit.parse_sources_md(HEADER + TABLE, "content/images")
        self.assertEqual(audit.check_records(entries, idx), [
            "content/images/portrait/hunjang.png: 생성 기록 행 없음(원본 content/images/sprite/hunjang.png)",
            "content/images/sprite/hunjang.png: 생성 기록 행 없음"])

    def test_one_column_can_cover_prompt_and_negative(self):
        """A-08-04 표처럼 "입력·금지 지시" 한 칸이 프롬프트·부정 프롬프트를 함께 채울 수 있다."""
        table = ("| 결과 파일 | 도구 | 모델·버전 | 시드 | 생성 시각 UTC | 입력·금지 지시 |\n|---|---|---|---|---|---|\n"
                 "| `content/images/map/village.png` | image_gen | 도구 미제공 | 도구 미제공 | 2026-11-08T00:00Z | 아래 전문 |\n")
        e = AssetEntry("map", "village", "content/images/map/village.png", "u", 1280, 720, placeholder=False)
        self.assertEqual(audit.check_records([e], audit.parse_sources_md(HEADER + table, "content/images")), [])

    def test_reference_column_not_indexed_as_output(self):
        """표정 sprite 행의 기준 그림 칸에 적은 기본 sprite 경로는 기본 sprite의 기록으로 보지 않는다(Codex 리뷰)."""
        table = ("| 결과 파일 | 도구 | 모델·버전 | 프롬프트 전문 | 부정 프롬프트 | 시드 | 생성일 | 기준 그림 파일 |\n"
                 "|---|---|---|---|---|---|---|---|\n")
        base = "content/images/sprite/hunjang.png"
        joy = "content/images/sprite/hunjang__joy.png"
        body = HEADER + table + _row(base).rstrip("\n") + " - |\n" + _row(joy).rstrip("\n") + f" `{base}` |\n"
        idx = audit.parse_sources_md(body, "content/images")
        self.assertEqual(idx.duplicates, [])
        self.assertEqual(idx.rows[base]["기준 그림 파일"], "-")
        self.assertEqual(idx.rows[joy]["기준 그림 파일"], f"`{base}`")

    def test_duplicate_rows_reported(self):
        e = AssetEntry("map", "village", "content/images/map/village.png", "u", 1280, 720, placeholder=False)
        idx = audit.parse_sources_md(HEADER + TABLE + _row(e.path) + _row(e.path), "content/images")
        self.assertIn("content/images/map/village.png: 생성 기록 행이 둘 이상", audit.check_records([e], idx))


class MapRegionTest(_Repo):
    def test_generated_regions_uniform_and_inside(self):
        boxes = dict(map_regions(sources.load_place_ids(), (1280, 720), (128, 128)))
        self.assertEqual(set(boxes), set(sources.load_place_ids()))
        self.assertEqual(sorted(x for x, _, _, _ in boxes.values()), [64, 320, 576, 832, 1088])
        self.assertTrue(all(y == 296 for _, y, _, _ in boxes.values()))

    def test_regions_missing(self):
        self.edit_manifest(lambda items: next(d for d in items if d["kind"] == "map").pop("regions"))
        r = check(self.repo, placeholder_mode=True)
        self.assertEqual(r.region_errors, ["map:village: 장소 좌표(regions) 없음"])
        self.assertFalse(r.ok)

    def test_regions_overlap_outside_and_missing_place(self):
        def fn(items):
            d = next(d for d in items if d["kind"] == "map")
            d["regions"] = {"well": [0, 0, 200, 200], "shrine": [100, 100, 50, 50], "tavern": [1200, 0, 128, 128],
                            "gate": [0, 600, 128, 128]}
        self.edit_manifest(fn)
        errs = check(self.repo, placeholder_mode=True).region_errors
        self.assertTrue(any("≠ 장소" in e for e in errs), errs)
        self.assertTrue(any("shrine와 well 상자가 겹침" in e for e in errs), errs)
        self.assertTrue(any("tavern 상자" in e and "밖" in e for e in errs), errs)

    def test_bad_region_value_type(self):
        self.edit_manifest(lambda items: next(d for d in items if d["kind"] == "map")["regions"].update(
            {"well": ["a", 0, 1, 1]}))
        errs = check(self.repo, placeholder_mode=True).region_errors
        self.assertTrue(any("well 상자" in e for e in errs), errs)

    def test_too_many_places_rejected(self):
        with self.assertRaises(SpecError):
            map_regions([f"p{i}" for i in range(11)], (1280, 720), (128, 128))


class CliTest(_Repo):
    def test_exit_codes(self):
        self.assertEqual(_quiet(["--repo", str(self.repo), "--placeholder"]), 0)
        self.assertEqual(_quiet(["--repo", str(self.repo)]), 1)
        self.assertEqual(_quiet(["--repo", str(self.repo), "--allow-placeholder", "place,ui,map,ad"]), 1)
        self.assertEqual(_quiet(["--repo", str(self.repo), "--allow-placeholder", "place,unknown"]), 2)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            _quiet(["--repo", str(self.repo), "--placeholder", "--allow-placeholder", "ui"])

    def test_result_json(self):
        self.assertEqual(_quiet(["--repo", str(self.repo), "--placeholder", "--result"]), 0)
        body = json.loads((self.repo / "docs/evidence/perf/E-08-07/check_assets.json").read_text(encoding="utf-8"))
        self.assertEqual(body["task"], "E-08-07")
        self.assertEqual(body["metrics"]["placeholder_count"]["value"], 102)
        self.assertIn("--placeholder", body["source"])

    def test_inputs_from_selected_repo(self):
        """`--repo`의 규격표·묶음·캠페인이 있으면 그것을, 없으면 이 저장소의 기본 파일을 쓴다(Codex 리뷰)."""
        from tools.assets.check_assets import input_paths
        self.assertEqual(input_paths(self.repo)["spec_path"], sources.SPEC_PATH)
        spec = self.repo / "docs/assets/spec.md"
        spec.parent.mkdir(parents=True)
        spec.write_text(sources.SPEC_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(input_paths(self.repo)["spec_path"], spec)

    def test_parse_allowed(self):
        self.assertEqual(audit.parse_allowed("place, ui"), frozenset({"place", "ui"}))
        self.assertEqual(audit.parse_allowed(""), frozenset())
        with self.assertRaises(ValueError):
            audit.parse_allowed("portraits")


class CommittedSetTest(unittest.TestCase):
    def test_repository_placeholder_set_passes(self):
        """저장소의 커밋된 세트가 `--placeholder`로 통과한다(4번). tests/assets라 로컬 CI core unittest에 들어간다."""
        r = check(ROOT, placeholder_mode=True)
        self.assertTrue(r.ok, format_report(r))


if __name__ == "__main__":
    unittest.main()
