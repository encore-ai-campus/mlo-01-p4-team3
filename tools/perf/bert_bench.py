"""D-05-02 CPU model benchmark CLI. ML dependencies stay in intent/."""
import subprocess
import sys


def main(argv=None) -> int:
    """Delegate measurement to the ML layer with UTF-8 output and this interpreter."""
    return subprocess.call([sys.executable, "-X", "utf8", "-m", "intent.bert_bench",
                            *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
