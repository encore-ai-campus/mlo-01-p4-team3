"""테스트 폴더 하나를 `unittest discover`처럼 돌리되, 지정한 테스트 id를 빼고 돌린다(precheck 무거운 테스트 생략, 11/9).

    python -m tools.run_tests -s tests/engine -t . --skip tests.engine.test_setup.AllPresets.test_serde_round_trip

`unittest discover`와 같은 탐색(`load_tests` 규칙 포함)과 출력(`Ran N tests`)을 쓴다. 실제로 뺀 개수를
`precheck-skip: N` 줄로 낸다(이름이 없는 id는 세지 않는다). 종료 코드는 `unittest`와 같다: 통과 0, 실패 1, 돈 테스트 0개 5.

계층: `drop_tests`(도메인, 테스트 묶음 거르기) / `main`(표현, 탐색·실행).
표준 라이브러리만 쓴다.
"""
from __future__ import annotations

import argparse
import sys
import unittest

SKIP_LINE = "precheck-skip:"


def drop_tests(suite: unittest.TestSuite, ids: set[str]) -> tuple[unittest.TestSuite, int]:
    """묶음에서 id가 `ids`에 든 테스트를 뺀 새 묶음과 뺀 개수. 묶음 구조(모듈·클래스 단위 준비 코드)는 유지한다."""
    out = unittest.TestSuite()
    removed = 0
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            sub, n = drop_tests(item, ids)
            removed += n
            out.addTest(sub)
        elif item.id() in ids:
            removed += 1
        else:
            out.addTest(item)
    return out, removed


def main(argv: list[str] | None = None) -> int:
    """명령줄 진입점."""
    ap = argparse.ArgumentParser(prog="python -m tools.run_tests", description="테스트 폴더 실행(지정 테스트 생략)")
    ap.add_argument("-s", "--start", required=True, help="탐색 시작 폴더(예: tests/engine)")
    ap.add_argument("-t", "--top", default=".", help="최상위 폴더(기본 .)")
    ap.add_argument("--skip", action="append", default=[], metavar="ID", help="뺄 테스트 id(여러 번)")
    args = ap.parse_args(argv)
    suite = unittest.defaultTestLoader.discover(args.start, top_level_dir=args.top)
    suite, removed = drop_tests(suite, set(args.skip))
    print(f"{SKIP_LINE} {removed}", file=sys.stderr, flush=True)
    result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
    if not result.wasSuccessful():
        return 1
    return 5 if result.testsRun == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
