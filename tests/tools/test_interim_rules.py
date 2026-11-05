"""임시 운영 판정 규칙(tools/interim_rules.py) 테스트. README 10-1절."""
import unittest

from tools import interim_rules as r

SHA = "a" * 40
SHA2 = "b" * 40
LEDGER = """# 원장

## 원장

| 태스크 | 작성 흐름 | 제출 번호 | 제출 HEAD SHA | 검토 흐름 | 검토 SHA | 판정 | 병합 SHA | 로컬 CI |
|---|---|---|---|---|---|---|---|---|
| E-05-01 | E | 01 | `{sha}` | B | `{sha}` | 통과 | `1234567890ab` | 통과(11/5 18:00) |

## 다른 절

| 표 | 아님 |
|---|---|
| x | y |
""".format(sha=SHA)


class BundleNameTest(unittest.TestCase):
    def test_roundtrip(self):
        name = r.bundle_filename("A-05-01", 3, SHA)
        self.assertEqual(name, "A-05-01_03_aaaaaaaaaaaa.bundle")
        b = r.parse_bundle_name(name)
        self.assertEqual((b.task_id, b.number, b.sha12), ("A-05-01", 3, "a" * 12))
        self.assertEqual(b.filename, name)

    def test_rejects_part_and_bad_names(self):
        for bad in ("A-05-01_03_aaaaaaaaaaaa.bundle.part", "A-05-01_3_aaaaaaaaaaaa.bundle",
                    "X-05-01_03_aaaaaaaaaaaa.bundle", "A-05-01_03_AAAAAAAAAAAA.bundle"):
            self.assertIsNone(r.parse_bundle_name(bad), bad)

    def test_number_range(self):
        with self.assertRaises(ValueError):
            r.bundle_filename("A-05-01", 0, SHA)

    def test_next_number_ignores_other_tasks_and_part(self):
        names = ["A-05-01_01_aaaaaaaaaaaa.bundle", "A-05-01_02_bbbbbbbbbbbb.bundle",
                 "A-05-01_05_cccccccccccc.bundle.part", "B-05-01_07_aaaaaaaaaaaa.bundle", "x.ci.txt"]
        self.assertEqual(r.next_submission_number(names, "A-05-01"), 3)
        self.assertEqual(r.next_submission_number(names, "C-05-01"), 1)

    def test_latest_wins(self):
        names = ["A-05-01_01_aaaaaaaaaaaa.bundle", "A-05-01_02_bbbbbbbbbbbb.bundle"]
        self.assertEqual(r.latest_submissions(names)["A-05-01"].number, 2)

    def test_newer_numbers(self):
        refs = ["refs/inbox/A-05-01/01", "refs/inbox/A-05-01/03", "refs/inbox/A-05-01/x"]
        self.assertEqual(r.newer_numbers(refs, 1), [3])
        self.assertEqual(r.newer_numbers(refs, 3), [])

    def test_main_bundle_name(self):
        name = r.main_bundle_filename("1105", "1830", SHA)
        self.assertRegex(name, r.MAIN_BUNDLE_RE)


class MergeMessageTest(unittest.TestCase):
    def test_no_closing_keyword(self):
        self.assertEqual(r.merge_message("A-05-01", 2), "Task: A-05-01 (제출 02)")
        self.assertEqual(r.merge_message("A-05-01", 1, "fix"), "Task: A-05-01 (제출 01, fix)")
        with self.assertRaises(ValueError):
            r.merge_message("A-05-01", 1, "feat")


class ServiceCheckTest(unittest.TestCase):
    def test_service_area_needs_service_tests(self):
        self.assertTrue(r.service_check(["server/api/views.py"], service_ran=False))
        self.assertTrue(r.service_check(["tests/ads/test_x.py"], service_ran=False))
        self.assertEqual(r.service_check(["server/api/views.py"], service_ran=True), [])
        self.assertEqual(r.service_check(["engine/x.py", "tests/tools/t.py"], service_ran=False), [])


class HoursTest(unittest.TestCase):
    def test_hours(self):
        self.assertEqual(r.parse_hours("2.5"), [])
        self.assertTrue(r.parse_hours(None))
        self.assertTrue(r.parse_hours("2h"))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        rows, errors = r.parse_ledger(LEDGER)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.row = rows[0]

    def test_parse(self):
        self.assertEqual((self.row.task_id, self.row.number, self.row.submit_sha, self.row.merge_sha),
                         ("E-05-01", 1, SHA, "1234567890ab"))

    def test_ok(self):
        self.assertEqual(r.check_ledger_row(self.row, "E", True, True), [])

    def test_same_flow_reviewer_fails(self):
        row = r.LedgerRow(**{**self.row.__dict__, "review_flow": "E"})
        self.assertTrue(any("⑧" in e for e in r.check_ledger_row(row, "E", True, True)))

    def test_author_must_match_evidence(self):
        errs = r.check_ledger_row(self.row, "B", True, True)
        self.assertTrue(any("증빙 작성자" in e for e in errs))
        self.assertTrue(any("⑧" in e for e in errs))  # 증빙 기준 작성 흐름 B = 검토 흐름 B

    def test_review_sha_must_equal_submit(self):
        row = r.LedgerRow(**{**self.row.__dict__, "review_sha": SHA2})
        self.assertTrue(any("검토 SHA" in e for e in r.check_ledger_row(row, "E", True, True)))

    def test_merge_must_be_in_main_with_submit_parent(self):
        errs = r.check_ledger_row(self.row, "E", False, False)
        self.assertTrue(any("main에 포함" in e for e in errs))
        self.assertTrue(any("부모" in e for e in errs))

    def test_verdict_and_ci(self):
        row = r.LedgerRow(**{**self.row.__dict__, "verdict": "좋음", "ci": "실패"})
        self.assertEqual(len(r.check_ledger_row(row, "E", True, True)), 2)

    def test_rejected_review_cannot_be_merged(self):
        row = r.LedgerRow(**{**self.row.__dict__, "verdict": "반려"})
        self.assertTrue(any("반려" in e for e in r.check_ledger_row(row, "E", True, True)))

    def test_bad_column_count(self):
        _, errors = r.parse_ledger("## 원장\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
        self.assertTrue(errors)


class StatusTest(unittest.TestCase):
    def test_status(self):
        self.assertEqual(r.task_status(True, False, True, True, True), "완료")
        self.assertEqual(r.task_status(True, False, False, True, True), "병합됨")  # 검토 기록 없음
        self.assertEqual(r.task_status(False, False, False, True, True), "병합됨")
        self.assertEqual(r.task_status(False, False, False, False, True), "제출됨")
        self.assertEqual(r.task_status(False, False, False, False, False), "미착수")
        self.assertEqual(r.task_status(True, True, True, True, True), "실패")


if __name__ == "__main__":
    unittest.main()
