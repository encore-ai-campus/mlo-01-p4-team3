"""표준 라이브러리 CLI 진입점: 서비스 의존 코드는 infra 프로세스에서 실행한다."""
import subprocess
import sys


def main(argv=None):
    """같은 Python의 인프라 명령을 실행하고 종료 코드를 전달한다."""
    result=subprocess.run([sys.executable, '-m', 'server.infra.load_content', *(sys.argv[1:] if argv is None else argv)])
    if result.returncode: raise SystemExit(result.returncode)


if __name__=='__main__':main()
