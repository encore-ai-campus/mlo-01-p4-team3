"""임시 에셋 세트 생성(E-05-05). 규격표 + 콘텐츠 ID 목록 → 실제와 같은 경로·이름·크기·형식의 임시 PNG와 manifest.json.

    python -m tools.assets.make_placeholders                 # 생성 + 대조 출력
    python -m tools.assets.make_placeholders --check         # 쓰지 않고 대조만
    python -m tools.assets.make_placeholders --result        # 대조 수치를 docs/evidence/perf/E-05-05/placeholders.json에

- 초상은 코드로 그린 도트 그림(`portrait_art.py`), 나머지는 단색 + 도형. 글자 없음. 같은 입력이면 같은 바이트.
- manifest에서 `placeholder: false`(실제 파일)인 항목은 덮어쓰지 않는다.
- 끝에 유형별 기대 수·manifest 항목 수·파일 수와 누락·남는 목록을 출력한다. 하나라도 어긋나면 종료 코드 1.

계층: 이 파일은 응용(흐름 조립) + 표현(CLI). 규칙은 `spec.py`·`art.py`·`portrait_art.py`(도메인), 입출력은 `sources.py`·`png.py`(인프라).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tools.assets import art, png, sources
from tools.assets.spec import KINDS, AssetEntry, Reconciliation, SpecError, expected_entries, key_label, reconcile

TASK_ID = "E-05-05"
RESULT_NAME = "placeholders"


@dataclass
class RunResult:
    """한 번 실행한 결과."""
    entries: list[AssetEntry]
    written: int
    kept_real: int
    max_file_bytes: int
    recon: Reconciliation
    bad_files: list[str] = field(default_factory=list)
    protected_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """대조 일치 + 모든 파일이 PNG·규격 크기·용량 상한 이내 + 덮어쓰지 않고 남긴 파일 없음."""
        return self.recon.ok and not self.bad_files and not self.protected_files


def build_expected(spec, bundle_path: Path, campaigns_path: Path) -> list[AssetEntry]:
    """입력 파일들을 읽어 기대 항목을 계산한다."""
    campaigns, slot_sizes = sources.load_campaigns(campaigns_path)
    return expected_entries(spec, sources.load_npc_ids(bundle_path), sources.load_place_ids(),
                            campaigns, slot_sizes, sources.slot_image_path)


def run(repo: Path, spec_path: Path = sources.SPEC_PATH, bundle_path: Path = sources.BUNDLE_PATH,
        campaigns_path: Path = sources.CAMPAIGNS_PATH, write: bool = True,
        logger: sources.JsonLogger | None = None, force: bool = False) -> RunResult:
    """임시 세트를 만들고(write=False면 건너뜀) 기대 ↔ manifest ↔ 파일을 대조한다.

    `placeholder: true` 항목이라도 디스크의 파일이 지금 만들 임시 그림과 다르면 덮어쓰지 않고
    `protected_files`로 보고한다(실제 파일을 넣고 표시를 안 바꾼 경우). `force=True`면 덮어쓴다.
    """
    spec = sources.load_spec(spec_path)
    expected = build_expected(spec, bundle_path, campaigns_path)
    existing = {e.key: e for e in sources.load_manifest(repo, spec)}
    final: list[AssetEntry] = []
    written = kept = 0
    protected: list[str] = []
    for e in expected:
        old = existing.get(e.key)
        if old is not None and not old.placeholder:
            if old.path != e.path or old.url != e.url:
                raise SpecError(f"{key_label(e.key)}: 실제 파일 항목의 path/url이 규격과 다름 ({old.path}, {old.url})")
            final.append(old)
            kept += 1
            continue
        final.append(e)
        if write:
            canvas = art.render(e.kind, e.id, e.width, e.height)
            data = png.encode_rgb(e.width, e.height, canvas.rows)
            if len(data) > spec.max_bytes:
                raise SpecError(f"{e.path}: {len(data)}바이트 > 상한 {spec.max_bytes}")
            current = repo / e.path
            if current.is_file() and current.read_bytes() != data and not force:
                # 임시 그림은 결정적이라, 내용이 다르면 표시(placeholder)를 안 바꾼 실제 파일일 수 있다
                protected.append(e.path)
                continue
            sources.write_file(repo, e.path, data)
            written += 1
    expected_keys = {e.key for e in expected}
    for key, old in existing.items():        # 기대에서 빠진 실제 파일 항목도 지우지 않는다(다시 생기면 보호). 대조에서 extra로 보고
        if key not in expected_keys and not old.placeholder:
            final.append(old)
    if write:
        sources.write_manifest(repo, spec, final)
    manifest_now = sources.load_manifest(repo, spec)
    recon = reconcile(expected, manifest_now, sources.list_asset_files(repo, spec), spec.root)
    max_bytes, bad = inspect_files(repo, final, spec.max_bytes)
    result = RunResult(final, written, kept, max_bytes, recon, bad, protected)
    if logger:
        logger.log("info", "placeholders_done" if write else "placeholders_checked",
                   written=written, kept_real=kept, expected=len(expected),
                   manifest=len(manifest_now), max_file_bytes=max_bytes, ok=result.ok)
        for b in bad:
            logger.log("warning", "bad_file", detail=b)
        for pth in protected:
            logger.log("warning", "protected_file", path=pth,
                       detail="placeholder: true인데 임시 그림과 다름. 실제 파일이면 manifest를 false로, 아니면 --force")
    return result


def inspect_files(repo: Path, entries: list[AssetEntry], max_bytes: int) -> tuple[int, list[str]]:
    """항목 파일마다 PNG 여부·픽셀 크기·용량을 본다. (가장 큰 파일 바이트, 위반 목록)."""
    biggest, bad = 0, []
    for e in entries:
        p = repo / e.path
        if not p.is_file():
            continue                        # 누락은 reconcile이 잡는다
        data = p.read_bytes()
        biggest = max(biggest, len(data))
        try:
            size = png.read_size(data)
        except ValueError:
            bad.append(f"{e.path}: PNG 아님")
            continue
        if size != (e.width, e.height):
            bad.append(f"{e.path}: 크기 {size[0]}x{size[1]} ≠ {e.width}x{e.height}")
        if len(data) > max_bytes:
            bad.append(f"{e.path}: {len(data)}바이트 > {max_bytes}")
    return biggest, bad


def format_report(r: RunResult) -> str:
    """유형별 수 표와 누락·남는 목록(사람이 읽는 출력)."""
    rc = r.recon
    lines = ["kind      expected  manifest  files  same"]
    for k in KINDS:
        e, m, f = rc.expected.get(k, 0), rc.manifest.get(k, 0), rc.files.get(k, 0)
        lines.append(f"{k:<9} {e:>8}  {m:>8}  {f:>5}  {'yes' if e == m == f else 'NO'}")
    te, tm, tf = (sum(d.values()) for d in (rc.expected, rc.manifest, rc.files))
    lines.append(f"{'total':<9} {te:>8}  {tm:>8}  {tf:>5}  {'yes' if te == tm == tf else 'NO'}")
    for title, items in (("missing_in_manifest", rc.missing_in_manifest), ("extra_in_manifest", rc.extra_in_manifest),
                         ("manifest_mismatch", rc.manifest_mismatch),
                         ("missing_files", rc.missing_files), ("extra_files", rc.extra_files)):
        lines.append(f"{title}: {len(items)}" + ("" if not items else " " + ", ".join(items)))
    lines.append(f"protected_files: {len(r.protected_files)}"
                 + ("" if not r.protected_files else " " + ", ".join(r.protected_files)))
    lines.append(f"bad_files: {len(r.bad_files)}" + ("" if not r.bad_files else " " + "; ".join(r.bad_files)))
    lines.append(f"written={r.written} kept_real={r.kept_real} max_file_bytes={r.max_file_bytes}")
    lines.append("RESULT: " + ("OK" if r.ok else "MISMATCH"))
    return "\n".join(lines)


def result_metrics(r: RunResult) -> dict:
    """결과 JSON `metrics`(README 4절 형식)."""
    rc = r.recon
    m = {}
    for k in KINDS:
        m[f"expected_{k}"] = {"value": rc.expected.get(k, 0), "unit": "개"}
    m["expected_total"] = {"value": sum(rc.expected.values()), "unit": "개"}
    m["manifest_total"] = {"value": sum(rc.manifest.values()), "unit": "개"}
    m["files_total"] = {"value": sum(rc.files.values()), "unit": "개"}
    m["missing_count"] = {"value": len(rc.missing_in_manifest) + len(rc.missing_files), "unit": "건"}
    m["extra_count"] = {"value": len(rc.extra_in_manifest) + len(rc.extra_files), "unit": "건"}
    m["manifest_mismatch_count"] = {"value": len(rc.manifest_mismatch), "unit": "건"}
    m["bad_file_count"] = {"value": len(r.bad_files), "unit": "건"}
    m["max_file_bytes"] = {"value": r.max_file_bytes, "unit": "바이트"}
    return m


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="임시 에셋 세트 생성과 대조(E-05-05)")
    ap.add_argument("--repo", type=Path, default=sources.ROOT, help="출력 저장소 루트(기본: 이 저장소)")
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 대조만")
    ap.add_argument("--force", action="store_true",
                    help="placeholder: true 항목은 내용이 달라도 임시 그림으로 덮어쓴다(실제 파일 보호 해제)")
    ap.add_argument("--result", action="store_true", help=f"결과 JSON을 docs/evidence/perf/{TASK_ID}/{RESULT_NAME}.json에")
    args = ap.parse_args(argv)
    logger = sources.JsonLogger(args.repo)
    try:
        r = run(args.repo, write=not args.check, logger=logger, force=args.force)
    except (SpecError, OSError, KeyError, ValueError) as exc:
        logger.log("error", "placeholders_failed", error_type=type(exc).__name__, error=str(exc))
        return 2
    print(format_report(r))
    if args.result:
        cmd = "python -m tools.assets.make_placeholders" + (" --check" if args.check else "") + " --result"
        path = sources.write_result(args.repo, TASK_ID, RESULT_NAME, result_metrics(r), cmd)
        logger.log("info", "result_written", path=str(path))
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
