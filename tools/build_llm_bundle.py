"""LLM MVP 제출 묶음 zip 만들기(E-07-06 완료 조건 1).

    python -m tools.build_llm_bundle --out out/llm_mvp/

강사가 받는 zip에는 MVP 노트북(`llm/colab/paldo_llm_mvp.ipynb`)이 부르는 순수 규칙 3개와 평가 세트만 넣는다:
`llm/prompt.py`, `llm/validator.py`, `llm/mvp_metrics.py`(`run_eval`·지표 함수), `interrogation_eval.json`.
`llm/__init__.py`는 넣지 않는다. 풀면 `llm`은 네임스페이스 패키지로 import된다.

- 결정성: 같은 커밋이면 같은 바이트. 파일 순서 정렬, 시각 1980-01-01 00:00:00, 권한 0o644, DEFLATE 수준 9로 고정한다.
- 비밀값: 넣을 파일마다 `check_secrets.scan_text`를 돌려 하나라도 걸리면 zip을 만들지 않고 실패한다.
- `tools`는 `llm`을 import하지 않는다(`tests/contract/test_layers.py`). 파일은 경로로 읽어 바이트 그대로 넣는다.

계층: 표현·응용(인자 파싱 → 파일 수집 → 검사 → zip 쓰기). 표준 라이브러리와 `tools`만 쓴다.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

from tools.check_secrets import scan_text
from tools.gitrepo import ROOT

ZIP_NAME = "paldo_llm_mvp.zip"
# zip 안 경로 → 저장소 상대 경로. 노트북은 zip을 푼 폴더를 sys.path에 넣고 이 경로로 읽는다.
MEMBERS = {
    "llm/prompt.py": "llm/prompt.py",
    "llm/validator.py": "llm/validator.py",
    "llm/mvp_metrics.py": "llm/mvp_metrics.py",
    "interrogation_eval.json": "fixtures/llm/interrogation_eval.json",
}
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
FILE_MODE = 0o100644   # 일반 파일, rw-r--r--
COMPRESS_LEVEL = 9


class BundleError(RuntimeError):
    """묶음을 만들 수 없다(파일 없음, 비밀값 검출)."""


def collect(root: Path = ROOT) -> list[tuple[str, bytes]]:
    """(zip 안 경로, 바이트) 목록을 zip 안 경로 순으로 돌려준다. 파일이 없으면 BundleError."""
    out = []
    for arc in sorted(MEMBERS):
        src = Path(root) / MEMBERS[arc]
        if not src.is_file():
            raise BundleError(f"파일 없음: {MEMBERS[arc]}")
        out.append((arc, src.read_bytes()))
    return out


def secret_findings(files: list[tuple[str, bytes]]) -> list[str]:
    """파일마다 `scan_text`를 돌린 결과(`경로:줄 사유`). UTF-8이 아니면 그것도 걸린 것으로 센다."""
    found = []
    for arc, data in files:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            found.append(f"{arc}: UTF-8 텍스트가 아님")
            continue
        found += [f"{arc}:{f.line} {f.reason}" for f in scan_text(MEMBERS[arc], text)]
    return found


def write_zip(files: list[tuple[str, bytes]], path: Path) -> None:
    """고정 시각·권한·압축으로 zip을 쓴다(같은 입력 = 같은 바이트)."""
    with zipfile.ZipFile(path, "w") as zf:
        for arc, data in files:
            info = zipfile.ZipInfo(arc, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = FILE_MODE << 16
            info.create_system = 3   # 유닉스: 권한 비트를 그대로 읽게 한다(만든 OS와 무관하게 같은 바이트)
            zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=COMPRESS_LEVEL)


def build(out_dir: Path | str, root: Path = ROOT) -> Path:
    """묶음을 검사하고 `<out_dir>/paldo_llm_mvp.zip`을 쓴다. 비밀값이 걸리면 쓰지 않고 BundleError."""
    files = collect(root)
    found = secret_findings(files)
    if found:
        raise BundleError("비밀값·접속 주소 검출, zip을 만들지 않음:\n" + "\n".join(found))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / ZIP_NAME
    write_zip(files, path)
    return path


def main(argv: list[str] | None = None) -> int:
    """인자를 읽고 zip을 만든 뒤 경로·파일 목록·sha256을 출력한다. 실패하면 1."""
    p = argparse.ArgumentParser(description="LLM MVP 제출 묶음 zip(E-07-06)")
    p.add_argument("--out", required=True, help="출력 폴더(예: out/llm_mvp/, 커밋하지 않는 위치)")
    a = p.parse_args(argv)
    try:
        path = build(a.out)
    except BundleError as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"zip: {path.as_posix()}")
    for arc in sorted(MEMBERS):
        print(f"  {arc}")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
