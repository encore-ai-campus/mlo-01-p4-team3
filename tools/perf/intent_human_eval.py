"""D-09-01 evaluation CLI; ML imports remain in intent/."""
import subprocess
import sys


def main(argv=None) -> int:
    """Run frozen-human or development evaluation in this Python's subprocess."""
    return subprocess.call([sys.executable, "-X", "utf8", "-m", "intent.human_eval",
                            *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
