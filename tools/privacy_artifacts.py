"""검토된 로컬 아티팩트 목록의 파일을 정확한 내용 해시로 확인하고 정리한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def purge_files(root, entries, *, apply=False):
    """승인된 파일만 대상으로 하며 내용이나 경로가 바뀌면 삭제 전에 중단한다."""
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor) or not entries:
        raise ValueError("explicit_artifact_directory_and_entries_required")
    planned = {}
    seen = set()
    missing = 0
    for row in entries:
        relative = Path(row["path"])
        digest = row["sha256"]
        if relative.is_absolute() or not relative.parts or ".." in relative.parts or relative.drive:
            raise ValueError("artifact_path_must_be_relative")
        if not re.fullmatch("[0-9a-f]{64}", digest):
            raise ValueError("artifact_sha256_required")
        path = root / relative
        if path in seen:
            raise ValueError("duplicate_artifact_path")
        seen.add(path)
        resolved = path.resolve()
        if resolved != path or not resolved.is_relative_to(root):
            raise ValueError("artifact_link_or_escape")
        if not path.exists():
            missing += 1
            continue
        if not path.is_file():
            raise ValueError("artifact_must_be_file")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError("artifact_hash_mismatch")
            planned[path] = os.fstat(stream.fileno())
    if apply:
        for path, observed in planned.items():
            if path.resolve() != path or path.stat() != observed:
                raise ValueError("artifact_changed_before_delete")
        for path in planned:
            path.unlink()
    return {"verified": len(planned), "already_missing": missing, "deleted": len(planned) if apply else 0}


def main(argv=None):
    """삭제 확인 없는 실행은 내용을 검사하고 건수만 출력한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--writers-drained", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.writers_drained:
        parser.error("privacy_artifact_writers_drain_required")
    if args.manifest.is_symlink() or (os.name != "nt" and args.manifest.stat().st_mode & 0o077):
        parser.error("privacy_protected_manifest_required")
    scope = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(json.dumps(purge_files(args.root, scope["files"], apply=args.apply)))


if __name__ == "__main__":
    main()
