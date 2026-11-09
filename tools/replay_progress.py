"""아웃박스에서 판 밖 진척을 복구하는 운영 명령의 진입점."""
import subprocess
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable, "-m", "server.infra.replay_progress", *sys.argv[1:]], cwd=root))
