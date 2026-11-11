"""사용자 테스트 출처 데이터 버전(의도 해석기 `freeze:`, 봇 보정 `ut-export:`)이 실제 입력 해시와 같을 때만 받아지는지 검사한다."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from intent import retrain as intent_retrain
from ml.suspicion import player_calibration
from ops.mlflow.retrain import validate
from pipeline.spark.privacy_filter import FIELDS
from tools.ut_freeze import freeze_data_version


class FreezeDataVersionTests(unittest.TestCase):
    """임시 동결 폴더로 재학습 실행 검사와 MLflow 재학습 설정 검사를 확인한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.freeze = root / "freeze"
        self.freeze.mkdir()
        (self.freeze / "manifest.json").write_bytes(b'{"freeze_version": "ut-freeze-1.1", "files": []}\n')
        self.version = "freeze:" + hashlib.sha256((self.freeze / "manifest.json").read_bytes()).hexdigest()
        self.tombstones = root / "deleted"
        self.tombstones.mkdir()
        self.out = root / "out"
        self.out.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_version_is_the_manifest_file_sha256(self):
        self.assertEqual(freeze_data_version(self.freeze), self.version)

    def intent_error(self, data_version, freeze=None):
        """재학습 명령의 인자 검사 오류 문장을 돌려준다. 무거운 학습 전에 멈춘다."""
        root = Path(self.tmp.name)
        argv = ["--freeze", str(freeze or self.freeze), "--train-labels", str(root / "t.jsonl"),
                "--eval-labels", str(root / "e.jsonl"), "--contexts", str(root / "c.jsonl"),
                "--tombstones", str(self.tombstones), "--cache", str(root / "cache"), "--out", str(self.out),
                "--registry-dir", str(root / "registry"), "--data-version", data_version, "--first-model"]
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            intent_retrain.main(argv)
        return stderr.getvalue()

    def test_intent_retrain_accepts_matching_freeze_and_existing_delta(self):
        """맞는 해시와 기존 Delta 형식은 버전 검사를 지나 다음 검사(이미 있는 출력 폴더)에서 멈춘다."""
        for value in (self.version, "delta:official-bronze@3"):
            self.assertIn("new private output directory", self.intent_error(value), value)

    def test_intent_retrain_rejects_mismatched_or_malformed_freeze(self):
        mismatch = "freeze:" + "0" * 64
        self.assertIn("sha256 of --freeze manifest.json", self.intent_error(mismatch))
        self.assertIn("sha256 of --freeze manifest.json",
                      self.intent_error(self.version, freeze=Path(self.tmp.name) / "missing"))
        for value in ("freeze:", "freeze:" + "A" * 64, self.version[:-1], "delta:bronze:42", "fixture:x"):
            self.assertIn("Delta snapshot or freeze manifest version", self.intent_error(value), value)

    def config(self, **versions):
        """세 모델 설정. 의도 해석기 학습 명령은 임시 동결 폴더를 가리킨다."""
        models = {}
        for family, module in (("intent", "intent.retrain"), ("suspicion", "ml.suspicion.retrain"),
                               ("ad_ctr", "ml.ad_ctr")):
            train = ["-m", module, "--out", "{output}"]
            if family == "intent":
                train = ["-m", module, "--freeze", str(self.freeze), "--tombstones", str(self.tombstones),
                         "--out", "{output}"]
            models[family] = {"model": "paldo-" + family, "data_version": versions.get(family, "delta:t@1"),
                              "feature_version": "f1", "train": train, "report": "decision.json",
                              "artifact": "model"}
        return {"models": models, "code_commit": "a" * 40, "tombstones": str(self.tombstones)}

    def test_runner_accepts_freeze_only_for_intent_with_matching_manifest(self):
        validate(self.config(intent=self.version))
        validate(self.config(intent="delta:official-bronze@3"))
        with self.assertRaisesRegex(ValueError, "versioned_delta_input_required"):
            validate(self.config(suspicion=self.version))
        with self.assertRaisesRegex(ValueError, "versioned_delta_input_required"):
            validate(self.config(ad_ctr=self.version))
        with self.assertRaisesRegex(ValueError, "freeze_manifest_mismatch"):
            validate(self.config(intent="freeze:" + "0" * 64))
        with self.assertRaisesRegex(ValueError, "versioned_delta_input_required"):
            validate(self.config(intent="freeze:" + "A" * 64))

    def test_runner_rejects_freeze_without_a_freeze_folder_argument(self):
        config = self.config(intent=self.version)
        config["models"]["intent"]["train"] = ["-m", "intent.retrain", "--tombstones", str(self.tombstones),
                                               "--out", "{output}"]
        with self.assertRaisesRegex(ValueError, "freeze_manifest_mismatch"):
            validate(config)
        config["models"]["intent"]["train"] = ["-m", "intent.retrain", "--tombstones", str(self.tombstones),
                                               "--freeze"]
        with self.assertRaisesRegex(ValueError, "freeze_manifest_mismatch"):
            validate(config)


class CalibrationDataVersionTests(unittest.TestCase):
    """봇 보정이 `ut-export:`를 실제 내보내기 파일 해시와 같을 때만 받고 입력 해시를 보고하는지 확인한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.games = root / "games.json"
        self.games.write_text(json.dumps({"games": [self.game("u%d" % i) for i in range(1, 5)]}), encoding="utf-8")
        self.version = "ut-export:" + hashlib.sha256(self.games.read_bytes()).hexdigest()
        self.consent = root / "consent.json"
        self.consent.write_text(json.dumps(["u1", "u2", "u3", "u4"]), encoding="utf-8")
        self.tombstones = root / "deleted"
        self.tombstones.mkdir()
        (self.tombstones / "request-1.json").write_text(
            json.dumps({field: [] for field in FIELDS}), encoding="utf-8")
        self.count = 0

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def game(user):
        def event(seq, kind, payload, at):
            return dict(event_id=f"{user}-{seq}", seq=seq, event_type=kind, occurred_at=at, payload=payload)

        return dict(player_pseudonym=user, source="human", events=[
            event(0, "game_started", {}, "2026-11-10T04:00:00Z"),
            event(1, "notebook_updated", {"entries": {"a": 20, "b": 80}}, "2026-11-10T04:00:01Z"),
            event(2, "votes_revealed", {"round": "main", "votes": {"mudang": "b", "a": "b", "b": "a"},
                                        "runoff_candidates": []}, "2026-11-10T04:00:02Z"),
            event(3, "game_ended", {}, "2026-11-10T04:00:03Z")])

    def run_calibration(self, data_version):
        self.count += 1
        out = Path(self.tmp.name) / f"report-{self.count}.json"
        argv = ["--games", str(self.games), "--consented-users", str(self.consent),
                "--tombstones", str(self.tombstones), "--out", str(out), "--data-version", data_version]
        with contextlib.redirect_stdout(io.StringIO()):
            player_calibration.main(argv)
        return json.loads(out.read_text(encoding="utf-8"))

    def calibration_error(self, data_version):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            self.run_calibration(data_version)
        return stderr.getvalue()

    def test_matching_export_version_is_reported_with_input_hashes(self):
        report = self.run_calibration(self.version)
        self.assertEqual(report["data_version"], self.version)
        self.assertEqual("ut-export:" + report["source_sha256"], self.version)
        self.assertEqual(report["consent_sha256"], hashlib.sha256(self.consent.read_bytes()).hexdigest())
        self.assertRegex(report["tombstones_sha256"], r"^[0-9a-f]{64}$")

    def test_existing_delta_and_fixture_versions_still_run(self):
        for value in ("delta:official-bronze@3", "fixture:ut-freeze-1.1"):
            self.assertEqual(self.run_calibration(value)["data_version"], value)

    def test_mismatched_or_malformed_export_versions_fail(self):
        self.assertIn("sha256 of the --games file", self.calibration_error("ut-export:" + "0" * 64))
        for value in ("ut-export:", "ut-export:" + "A" * 64, self.version[:-1], "freeze:" + "a" * 64, "x"):
            self.assertIn("Delta snapshot, the user-test export", self.calibration_error(value), value)

    def expected_tombstones_sha256(self):
        digest = hashlib.sha256()
        for file in sorted(self.tombstones.glob("request-*.json")):
            data = file.read_bytes()
            digest.update(f"{file.name}\0{len(data)}\0".encode("utf-8") + data)
        return digest.hexdigest()

    def test_tombstone_hash_is_exact_and_tracks_same_name_content(self):
        """파일 이름·길이·바이트를 정렬해 묶은 값이고, 같은 이름의 내용만 바뀌어도 달라진다."""
        (self.tombstones / "request-0.json").write_text(json.dumps({field: [] for field in FIELDS}),
                                                       encoding="utf-8")
        first = self.run_calibration(self.version)["tombstones_sha256"]
        self.assertEqual(first, self.expected_tombstones_sha256())
        (self.tombstones / "request-1.json").write_text(
            json.dumps({field: [] for field in FIELDS}, indent=1), encoding="utf-8")
        self.assertNotEqual(self.run_calibration(self.version)["tombstones_sha256"], first)

    def test_tombstone_bytes_changed_while_loading_stop_before_report(self):
        """플레이어 목록이 같아도 읽는 도중 요청 파일 바이트가 바뀌면 보고서를 쓰지 않는다."""
        from unittest.mock import patch

        real = player_calibration.load_tombstones

        def load_then_change(directory):
            result = real(directory)
            (self.tombstones / "request-1.json").write_text(
                json.dumps({field: ["s1"] if field != "players" else [] for field in FIELDS}), encoding="utf-8")
            return result

        with patch.object(player_calibration, "load_tombstones", side_effect=load_then_change):
            with self.assertRaisesRegex(ValueError, "tombstones changed"):
                self.run_calibration(self.version)
        self.assertFalse(list(Path(self.tmp.name).glob("report-*.json")))

    def test_stale_or_padded_export_versions_fail(self):
        """내보내기 파일이 바뀐 뒤의 이전 값과 해시 뒤에 글자를 붙인 값은 거부한다."""
        stale = self.version
        self.games.write_text(self.games.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertIn("sha256 of the --games file", self.calibration_error(stale))
        current = "ut-export:" + hashlib.sha256(self.games.read_bytes()).hexdigest()
        for value in (current + "\n", current + "0", " " + current):
            self.assertIn("Delta snapshot, the user-test export", self.calibration_error(value), repr(value))

    def test_tombstone_hash_changes_with_request_files(self):
        before = self.run_calibration(self.version)["tombstones_sha256"]
        (self.tombstones / "request-2.json").write_text(json.dumps({field: [] for field in FIELDS}),
                                                       encoding="utf-8")
        self.assertNotEqual(self.run_calibration(self.version)["tombstones_sha256"], before)


if __name__ == "__main__":
    unittest.main()
