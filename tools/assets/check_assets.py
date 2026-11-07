"""에셋 정식 검사(E-08-07): 에셋2(생성 기록)·에셋3(파일 규격)·에셋4(콘텐츠 ID와 1:1)를 한 번에 판정한다.

    python -m tools.assets.check_assets                                   # 실제 세트(모든 유형이 실제 파일이어야 함)
    python -m tools.assets.check_assets --placeholder                     # 임시 세트(모든 유형의 임시 파일 허용)
    python -m tools.assets.check_assets --allow-placeholder place,ui,map,ad   # 시연 기준(지정 유형만 임시 허용)
    python -m tools.assets.check_assets --placeholder --result            # 수치를 docs/evidence/perf/E-08-07/check_assets.json에

- 콘텐츠 ID 목록은 코드에 적지 않는다. 인물은 콘텐츠 묶음, 장소는 계약 `Place`, UI 아이콘·용량 상한·크기는 규격표
  (`docs/assets/spec.md`), 광고는 `fixtures/ads/campaigns.json`(지면 규격 `slot_sizes`)에서 읽는다(`make_placeholders`와 같은 입력).
- 임시 파일(`placeholder: true`)은 에셋2를 면제한다. 어느 모드든 실제 파일(`placeholder: false`)은 에셋2~4를 모두 본다.
  `--placeholder`는 임시 표시를 허용할 뿐 실제 파일 검사를 건너뛰지 않는다.
- 파일을 쓰지 않는다(`--result`의 결과 JSON만 예외). 하나라도 어긋나면 종료 코드 1, 입력 오류면 2.

계층: 이 파일은 응용(흐름 조립) + 표현(CLI). 규칙은 `audit.py`·`spec.py`·`vn.py`(도메인), 파일 읽기는 `sources.py`·`png.py`(인프라).
에셋3 파일 검사는 `make_placeholders.inspect_files`, 에셋4 대조는 `spec.reconcile`을 그대로 부른다(같은 규칙을 두 번 쓰지 않는다).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from tools.assets import audit, sources
from tools.assets.make_placeholders import build_expected, inspect_files
from tools.assets.spec import KINDS, Reconciliation, SpecError, reconcile

TASK_ID = "E-08-07"
RESULT_NAME = "check_assets"
SOURCES_NAME = "SOURCES.md"


@dataclass
class CheckResult:
    """검사 한 번의 결과. 목록마다 위반 문장."""
    mode: str
    recon: Reconciliation
    bad_files: list[str] = field(default_factory=list)
    record_errors: list[str] = field(default_factory=list)
    policy_errors: list[str] = field(default_factory=list)
    region_errors: list[str] = field(default_factory=list)
    max_file_bytes: int = 0
    real_count: int = 0
    placeholder_count: int = 0

    @property
    def ok(self) -> bool:
        """에셋2~4와 임시 허용 범위·지도 좌표 위반이 모두 0건이면 True."""
        return self.recon.ok and not (self.bad_files or self.record_errors or self.policy_errors
                                      or self.region_errors)


def check(repo: Path, placeholder_mode: bool = False, allowed: frozenset[str] = frozenset(),
          spec_path: Path = sources.SPEC_PATH, bundle_path: Path = sources.BUNDLE_PATH,
          campaigns_path: Path = sources.CAMPAIGNS_PATH) -> CheckResult:
    """저장소 `repo`의 manifest·파일·생성 기록을 규격표·콘텐츠 ID와 대조한다(쓰지 않음)."""
    spec = sources.load_spec(spec_path)
    expected = build_expected(spec, bundle_path, campaigns_path)
    manifest = sources.load_manifest(repo, spec)
    recon = reconcile(expected, manifest, sources.list_asset_files(repo, spec), spec.root)
    biggest, bad = inspect_files(repo, manifest, spec)
    src_path = repo / spec.root / SOURCES_NAME
    index = audit.parse_sources_md(src_path.read_text(encoding="utf-8"), spec.root) if src_path.is_file() else None
    mode = "placeholder" if placeholder_mode else ("allow:" + ",".join(sorted(allowed)) if allowed else "real")
    return CheckResult(
        mode=mode, recon=recon, bad_files=bad,
        record_errors=audit.check_records(manifest, index),
        policy_errors=audit.check_placeholder_policy(manifest, placeholder_mode, allowed),
        region_errors=audit.check_map_regions(manifest, sources.load_place_ids()),
        max_file_bytes=biggest,
        real_count=sum(1 for e in manifest if not e.placeholder),
        placeholder_count=sum(1 for e in manifest if e.placeholder))


def input_paths(repo: Path) -> dict[str, Path]:
    """규격표·콘텐츠 묶음·캠페인을 검사 대상 저장소 `repo` 기준으로 고른다. 그 저장소에 없으면 이 저장소의 기본 파일."""
    out = {}
    for name, default in (("spec_path", sources.SPEC_PATH), ("bundle_path", sources.BUNDLE_PATH),
                          ("campaigns_path", sources.CAMPAIGNS_PATH)):
        candidate = repo / default.relative_to(sources.ROOT)
        out[name] = candidate if candidate.is_file() else default
    return out


def format_report(r: CheckResult) -> str:
    """유형별 수 표와 위반 목록(사람이 읽는 출력)."""
    rc = r.recon
    lines = [f"mode: {r.mode}", "kind        expected  manifest  files  same"]
    for k in KINDS:
        e, m, f = rc.expected.get(k, 0), rc.manifest.get(k, 0), rc.files.get(k, 0)
        lines.append(f"{k:<11} {e:>8}  {m:>8}  {f:>5}  {'yes' if e == m == f else 'NO'}")
    sections = (
        ("에셋4 missing_in_manifest", rc.missing_in_manifest), ("에셋4 extra_in_manifest", rc.extra_in_manifest),
        ("에셋4 missing_files", rc.missing_files), ("에셋4 extra_files", rc.extra_files),
        ("에셋3 manifest_mismatch", rc.manifest_mismatch), ("에셋3 pair_mismatch", rc.pair_mismatch),
        ("에셋3 bad_files", r.bad_files), ("에셋2 records", r.record_errors),
        ("임시 허용 범위", r.policy_errors), ("지도 좌표", r.region_errors))
    for title, items in sections:
        lines.append(f"{title}: {len(items)}" + ("" if not items else "\n  " + "\n  ".join(items)))
    lines.append(f"real={r.real_count} placeholder={r.placeholder_count} max_file_bytes={r.max_file_bytes}")
    lines.append("RESULT: " + ("OK" if r.ok else "FAIL"))
    return "\n".join(lines)


def result_metrics(r: CheckResult) -> dict:
    """결과 JSON `metrics`(README 4절 형식)."""
    rc = r.recon
    return {
        "expected_total": {"value": sum(rc.expected.values()), "unit": "개"},
        "manifest_total": {"value": sum(rc.manifest.values()), "unit": "개"},
        "files_total": {"value": sum(rc.files.values()), "unit": "개"},
        "missing_count": {"value": len(rc.missing_in_manifest) + len(rc.missing_files), "unit": "건"},
        "extra_count": {"value": len(rc.extra_in_manifest) + len(rc.extra_files), "unit": "건"},
        "spec_violation_count": {"value": len(rc.manifest_mismatch) + len(rc.pair_mismatch) + len(r.bad_files),
                                 "unit": "건"},
        "record_missing_count": {"value": len(r.record_errors), "unit": "건"},
        "policy_violation_count": {"value": len(r.policy_errors), "unit": "건"},
        "map_region_error_count": {"value": len(r.region_errors), "unit": "건"},
        "real_count": {"value": r.real_count, "unit": "개"},
        "placeholder_count": {"value": r.placeholder_count, "unit": "개"},
        "max_file_bytes": {"value": r.max_file_bytes, "unit": "바이트"},
    }


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점."""
    ap = argparse.ArgumentParser(description="에셋 정식 검사: 에셋2·3·4(E-08-07)")
    ap.add_argument("--repo", type=Path, default=sources.ROOT, help="검사할 저장소 루트(기본: 이 저장소)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--placeholder", action="store_true", help="임시 세트: 모든 유형의 placeholder: true 허용")
    mode.add_argument("--allow-placeholder", metavar="KINDS", default="",
                      help="쉼표로 지정한 유형만 placeholder: true 허용(예: place,ui,map,ad)")
    ap.add_argument("--result", action="store_true", help=f"결과 JSON을 docs/evidence/perf/{TASK_ID}/{RESULT_NAME}.json에")
    args = ap.parse_args(argv)
    logger = sources.JsonLogger(args.repo, component="check_assets")
    try:
        allowed = audit.parse_allowed(args.allow_placeholder)
        r = check(args.repo, placeholder_mode=args.placeholder, allowed=allowed, **input_paths(args.repo))
    except (SpecError, OSError, KeyError, ValueError) as exc:
        logger.log("error", "check_assets_failed", error_type=type(exc).__name__, error=str(exc))
        return 2
    print(format_report(r))
    logger.log("info", "check_assets_done", mode=r.mode, ok=r.ok, real=r.real_count,
               placeholder=r.placeholder_count, violations=len(r.bad_files) + len(r.record_errors)
               + len(r.policy_errors) + len(r.region_errors))
    if args.result:
        flags = " --placeholder" if args.placeholder else (f" --allow-placeholder {args.allow_placeholder}"
                                                          if args.allow_placeholder else "")
        path = sources.write_result(args.repo, TASK_ID, RESULT_NAME, result_metrics(r),
                                    f"python -m tools.assets.check_assets{flags} --result")
        logger.log("info", "result_written", path=str(path))
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
