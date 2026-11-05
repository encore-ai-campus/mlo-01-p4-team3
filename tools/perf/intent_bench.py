"""D-05-01 benchmark CLI; ML work stays in intent/, tools/ uses the standard library.

    python -m tools.perf.intent_bench --n 1000 --seed 7
"""
import subprocess
import sys


def main(argv=None) -> int:
    """Run the ML benchmark with this interpreter and propagate its exit status."""
    args = sys.argv[1:] if argv is None else argv
    return subprocess.call([sys.executable, "-m", "intent.evaluation", *args])


if __name__ == "__main__":
    raise SystemExit(main())
