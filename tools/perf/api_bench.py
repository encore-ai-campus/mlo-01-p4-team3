"""B-06-02 API 부하 CLI. 외부 의존 코드는 시험 프로세스에서만 실행한다.

`--web-processes N[,M]`이 있으면 E-09-10 웹 프로세스 수 비교(server/tests/multiproc_bench.py)로 넘긴다.
"""
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    """저장소 루트에서 같은 Python으로 실제 Mongo 시험을 실행한다."""
    arguments=sys.argv[1:] if argv is None else argv
    if '--proxy-profile' in arguments:
        module='server.tests.proxy_bench'
    elif any(a.split('=', 1)[0] in ('--web-processes', '--serve-dir', '--load-dir') for a in arguments):
        module='server.tests.multiproc_bench'
    else:
        module='server.tests.api_bench'
    return subprocess.call([sys.executable, '-B', '-m', module,
                            *arguments], cwd=Path(__file__).resolve().parents[2])


if __name__ == '__main__':
    raise SystemExit(main())
