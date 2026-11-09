"""MLflow 등록 관문의 명령 진입점."""
import subprocess
import sys
from pathlib import Path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    raise SystemExit(subprocess.call([sys.executable, "-m", "ops.mlflow.gate", *sys.argv[1:]], cwd=root))
