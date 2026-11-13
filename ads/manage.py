"""광고 서버 전용 Django 관리 명령 진입점."""
import os
import sys
from pathlib import Path


def main():
    """기존 로컬 .env 형식을 읽고 광고 설정으로 관리 명령을 실행한다."""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from ops.compose.devenv import parse_env
    env_file = root / ".env"
    if env_file.exists():
        for name, value in parse_env(env_file.read_text(encoding="utf-8")).items():
            os.environ.setdefault(name, value)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ads.project.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
