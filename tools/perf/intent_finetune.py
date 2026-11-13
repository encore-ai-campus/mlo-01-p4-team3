"""D-07-03 미세조정 CLI. ML 패키지는 intent/ 계층에서 불러온다."""
import subprocess
import sys


def main(argv=None) -> int:
    """같은 Python의 별도 프로세스로 학습·측정을 실행한다."""
    return subprocess.call([sys.executable, "-X", "utf8", "-m", "intent.finetune",
                            *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
