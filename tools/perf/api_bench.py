"""B-06-02 API 부하 CLI. 외부 의존 코드는 시험 프로세스에서만 실행한다."""
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    """저장소 루트에서 같은 Python으로 실제 Mongo 시험을 실행한다."""
    return subprocess.call([sys.executable, '-B', '-m', 'server.tests.api_bench',
                            *(sys.argv[1:] if argv is None else argv)], cwd=Path(__file__).resolve().parents[2])


if __name__ == '__main__':
    raise SystemExit(main())
