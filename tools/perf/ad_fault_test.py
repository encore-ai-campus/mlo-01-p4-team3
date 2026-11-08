"""B-08-03 광고 장애 시험 진입점. 운영 설정은 시험 모듈을 import하지 않는다."""
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    """같은 Python으로 격리 Django·Mongo·Daphne 시험 프로세스를 실행한다."""
    return subprocess.call([sys.executable, '-B', '-m', 'server.tests.ad_fault_bench',
                            *(sys.argv[1:] if argv is None else argv)], cwd=Path(__file__).resolve().parents[2])


if __name__ == '__main__':
    raise SystemExit(main())
