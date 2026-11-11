"""ops/ 아래 Dockerfile의 RUN 명령이 줄바꿈 대신 엉뚱한 기호로 이어져 빌드가 깨지지 않는지 검사한다."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
_STRAY_JOIN = re.compile(r"\s\+\s+&&")


class DockerfileTests(unittest.TestCase):
    """이미지 정의 파일의 셸 연결 형식을 확인한다."""

    def test_run_lines_have_no_stray_plus_before_and(self):
        """`git libgomp1 +    && rm` 같은 줄은 `+`를 패키지 이름으로 읽어 빌드가 실패한다(11/11 재학습 이미지)."""
        files = sorted(ROOT.joinpath("ops").rglob("Dockerfile*"))
        self.assertTrue(files)
        bad = [f"{p.relative_to(ROOT)}:{n}" for p in files
               for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
               if _STRAY_JOIN.search(line)]
        self.assertEqual(bad, [])

    def test_retrain_image_continues_run_with_backslash(self):
        """재학습 이미지의 두 RUN 명령은 줄 끝 역슬래시로 다음 줄 `&&`에 이어진다."""
        lines = ROOT.joinpath("ops/k3s/images/retrain/Dockerfile").read_text(encoding="utf-8").splitlines()
        joined = [i for i, line in enumerate(lines) if line.startswith("RUN ") and line.endswith(" \\")]
        self.assertEqual(len(joined), 2)
        for i in joined:
            self.assertTrue(lines[i + 1].lstrip().startswith("&& "))


if __name__ == "__main__":
    unittest.main()
