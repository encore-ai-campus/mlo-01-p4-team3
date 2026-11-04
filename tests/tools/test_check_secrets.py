"""비밀정보 검사(tools/check_secrets.py) 테스트. E-05-02 조건 5."""
import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from tools.check_secrets import (
    EXEMPT_PREFIX, Finding, apply_allowlist, decode_text, is_secret_key, key_pieces, main,
    parse_allowlist, run, scan_text,
)
from tools.gitrepo import ROOT, GitRepo

FIXTURES = ROOT / "tests" / "fixtures" / "secrets"


def reasons(findings):
    return [f.reason for f in findings]


class FakeRepo:
    """추적 파일 목록과 내용만 가진 가짜 저장소."""

    def __init__(self, files: dict):
        self.files = files
        self.root = ROOT

    def tracked_files(self):
        return list(self.files)

    def read_bytes(self, path):
        v = self.files.get(path)
        return v if isinstance(v, bytes) or v is None else v.encode("utf-8")

    def read_text(self, path):
        v = self.files.get(path)
        return v.decode("utf-8") if isinstance(v, bytes) else v


class CurrentRepository(unittest.TestCase):
    def test_whole_repository_has_zero_findings(self):
        remaining, errors, scanned = run(GitRepo())
        self.assertGreater(scanned, 50)
        self.assertEqual([str(f) for f in remaining] + errors, [])


class Positives(unittest.TestCase):
    """탐지 유형마다 양성 사례(tests/fixtures/secrets/)."""

    def scan(self, name):
        path = FIXTURES / name
        return scan_text(f"{EXEMPT_PREFIX}{name}", path.read_text(encoding="utf-8"))

    def test_private_ip_ranges(self):
        found = self.scan("private_ip.txt")  # 10/8, 172.16/12, 192.168/16 각 1줄(2~4행)
        self.assertEqual([f.line for f in found], [2, 3, 4])
        self.assertTrue(all(r.startswith("사설 IP ") for r in reasons(found)))

    def test_private_key_header(self):
        self.assertEqual(reasons(self.scan("private_key.txt")), ["개인 키 헤더", "개인 키 헤더"])

    def test_key_value_in_code_quoted_only(self):
        keys = [f.key for f in self.scan("key_values.py")]
        self.assertEqual(keys, ["DB_PASSWORD", "api_key", "signingKey", "session_token"])  # 마지막은 보간 없는 상수 f-string

    def test_key_value_in_yaml(self):
        keys = [f.key for f in self.scan("key_values.yml")]
        self.assertEqual(keys, ["password", "accessToken"])

    def test_key_value_in_env(self):
        keys = [f.key for f in self.scan("key_values.env")]
        self.assertEqual(keys, ["GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY", "MYSQL_PWD"])

    def test_fixture_folder_is_excluded_from_repository_scan(self):
        repo = FakeRepo({f"{EXEMPT_PREFIX}a.txt": "password: real-looking-value\n"})
        remaining, errors, _ = run(repo)
        self.assertEqual((remaining, errors), ([], []))

    def test_same_value_outside_fixture_folder_fails(self):
        repo = FakeRepo({"config/app.yml": "password: real-looking-value\n"})
        remaining, _, _ = run(repo)
        self.assertEqual([f.key for f in remaining], ["password"])


class FalsePositives(unittest.TestCase):
    """오탐 방지 사례(E-05-02 조건 5에 적힌 것)."""

    def assertClean(self, path, text):
        self.assertEqual(scan_text(path, text), [], text)

    def test_secret_id_is_game_term(self):
        self.assertClean("x.json", '{"secret_id": "gambling", "secrets": "a"}\n')
        self.assertClean("x.py", 'secret_id = "gambling"\n')

    def test_optional_annotation(self):
        self.assertClean("x.py", "    serve_token: Optional[str] = None\n")

    def test_fstring_token(self):
        self.assertClean("x.py", '        token = f"fake.{req.request_id}"\n')

    def test_ttl_constant(self):
        self.assertClean("x.py", "TOKEN_TTL_MS = 30_000\n")

    def test_unlock_text_mentions_secret(self):
        self.assertClean("x.json", '      "unlock": "secret:gambling 고백",\n')

    def test_placeholders_and_env_refs(self):
        self.assertClean("x.yml", "password: ${DB_PASSWORD}\n")
        self.assertClean("x.yml", 'token: "<토큰>"\n')
        self.assertClean("x.env", "API_TOKEN=\n")
        self.assertClean("x.yml", "token: ${{ secrets.GH_TOKEN }}\n")
        self.assertClean("x.py", 'password = ""\n')

    def test_code_variables_and_calls_are_not_values(self):
        self.assertClean("x.py", "token = tokens[0]\n")
        self.assertClean("x.py", "password = os.environ['DB_PASSWORD']\n")
        self.assertClean("x.py", "def verify_token(token: str, key: bytes) -> bool:\n")

    def test_public_and_out_of_range_ips(self):
        self.assertClean("x.md", "8.8.8.8, 172.32.0.1, 192.169.1.1, 999.10.0.1\n")
        self.assertClean("x.md", "버전 2.10.0.1.5\n")  # 점 네 개보다 긴 숫자 열은 IP로 보지 않음


class KeyName(unittest.TestCase):
    def test_pieces(self):
        self.assertEqual(key_pieces("signingKey"), ["signing", "key"])
        self.assertEqual(key_pieces("AWS_SECRET_ACCESS_KEY"), ["aws", "secret", "access", "key"])
        self.assertEqual(key_pieces("db.password"), ["db", "password"])

    def test_last_piece_rule(self):
        for k in ("password", "db_passwd", "MYSQL_PWD", "clientSecret", "api-token", "apikey",
                  "credentials", "api_key", "hmacKey", "private_key"):
            self.assertTrue(is_secret_key(k), k)
        for k in ("secret_id", "secrets", "TOKEN_TTL_MS", "token_count", "key", "public_key", "passwordHint"):
            self.assertFalse(is_secret_key(k), k)


class Allowlist(unittest.TestCase):
    def test_format_errors(self):
        entries, errors = parse_allowlist("# 주석\na.json | token\nb/*.json | token | x\nc/ | token | x\nd.py | token | ok\n")
        self.assertEqual([e.path for e in entries], ["d.py"])
        self.assertEqual(len(errors), 3)

    def test_entry_allows_only_that_key_in_that_file(self):
        findings = [Finding("a.json", 1, "r", "token"), Finding("a.json", 2, "r", "password"),
                    Finding("b.json", 1, "r", "token"), Finding("a.json", 3, "사설 IP")]
        entries, _ = parse_allowlist("a.json | token | 가짜 이름\n")
        remaining, dead = apply_allowlist(findings, entries)
        self.assertEqual([(f.path, f.line) for f in remaining], [("a.json", 2), ("b.json", 1), ("a.json", 3)])
        self.assertEqual(dead, [])

    def test_dead_exception_fails(self):
        repo = FakeRepo({"tools/secrets_allowlist.txt": "a.json | token | 예전 예외\n", "a.json": '{"id": 1}\n'})
        remaining, errors, _ = run(repo)
        self.assertEqual(remaining, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("죽은 예외", errors[0])

    def test_real_allowlist_file_is_valid(self):
        _, errors = parse_allowlist((ROOT / "tools" / "secrets_allowlist.txt").read_text(encoding="utf-8"))
        self.assertEqual(errors, [])


class Binary(unittest.TestCase):
    def test_binary_is_skipped(self):
        self.assertIsNone(decode_text(b"\x89PNG\r\n\x1a\n\0\0password=abc"))
        self.assertIsNone(decode_text(b"\xff\xfe\xfa"))
        repo = FakeRepo({"img.png": b"\x89PNG\0password: abc"})
        self.assertEqual(run(repo)[2], 0)


class Cli(unittest.TestCase):
    def test_main_fails_with_file_and_line(self):
        repo = FakeRepo({"ops/app.env": "DB_PASSWORD=hunter2-fake\n"})
        out = io.StringIO()
        with redirect_stdout(out):
            code = main([], repo=repo)
        self.assertEqual(code, 1)
        self.assertIn("ops/app.env:1:", out.getvalue())


if __name__ == "__main__":
    unittest.main()
