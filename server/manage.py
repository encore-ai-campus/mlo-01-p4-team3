"""B 게임 웹의 Django 관리 명령 진입점."""
import os
import sys
from pathlib import Path


def main():
    """저장소 루트를 경로에 넣고 Django 명령을 실행한다."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "server.paldo_web.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
