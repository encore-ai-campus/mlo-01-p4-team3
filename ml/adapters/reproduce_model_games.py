"""모델 v0 고정 시드 판 재현 확인(D-08-04 A 쪽).

python -m ml.adapters.reproduce_model_games --suspicion-version <v> --intent-version <v> --games 50 --seed 1

`LocalModelSelector`로 고른 같은 모델·같은 시드로 `run_model_games`를 두 번 돌리고, 두 실행의 판 ID·모델 버전·
특징 버전·규칙 버전·이벤트 바이트(SHA-256)·판별 이벤트 수·판별 종료 결과(승자·사유)가 같은지 비교한다.
실행 시간·추론 지연·코드 커밋·출력 경로처럼 실행마다 달라도 되는 값은 비교하지 않는다.

계층: 도메인(`digest_events`, `compare_runs`, 입력은 dict 목록만), 인프라(`read_run`, 파일 읽기),
응용(`reproduce`, 실행 함수를 받아 두 번 돌리고 비교), 표현(`main`, CLI).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable

EVENTS_FILE = "events.jsonl"      # tools.run_engine_games.EVENTS_FILE와 같은 이름
MANIFEST_FILE = "manifest.json"   # tools.run_engine_games.MANIFEST_FILE와 같은 이름
COMPARED_MANIFEST_KEYS = ("game_ids", "model_versions", "feature_version", "rule_version", "preset_id", "seed",
                          "npc_policy_version", "events", "games")


@dataclass(frozen=True)
class RunDigest:
    """한 실행에서 재현 비교에 쓰는 값만 모은 요약."""
    manifest: dict
    events_sha256: str
    events_per_game: dict = field(default_factory=dict)
    results_per_game: dict = field(default_factory=dict)


def digest_events(events: Iterable[dict]) -> tuple[dict, dict]:
    """이벤트 봉투 목록에서 판별 이벤트 수와 종료 결과(승자·사유)를 뽑는다."""
    counts: dict[str, int] = {}
    results: dict[str, dict] = {}
    for e in events:
        gid = e.get("game_id")
        counts[gid] = counts.get(gid, 0) + 1
        if e.get("event_type") == "game_ended":
            payload = e.get("payload") or {}
            results[gid] = {"winner": payload.get("winner"), "reason": payload.get("reason")}
    return counts, results


def compare_runs(a: RunDigest, b: RunDigest) -> list[str]:
    """두 실행이 다른 항목 목록. 빈 목록이면 재현됨."""
    diffs = [f"manifest.{k}" for k in COMPARED_MANIFEST_KEYS if a.manifest.get(k) != b.manifest.get(k)]
    if a.events_sha256 != b.events_sha256:
        diffs.append("events_sha256")
    for gid in sorted(set(a.events_per_game) | set(b.events_per_game)):
        if a.events_per_game.get(gid) != b.events_per_game.get(gid):
            diffs.append(f"events_per_game.{gid}")
    for gid in sorted(set(a.results_per_game) | set(b.results_per_game)):
        if a.results_per_game.get(gid) != b.results_per_game.get(gid):
            diffs.append(f"result.{gid}")
    return diffs


def read_run(out: Path) -> RunDigest:
    """`run_model_games` 출력 폴더를 읽어 요약을 만든다. 파일이 없으면 OSError."""
    manifest = json.loads((out / MANIFEST_FILE).read_text(encoding="utf-8"))
    data = (out / EVENTS_FILE).read_bytes()
    events = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    counts, results = digest_events(events)
    return RunDigest(manifest, hashlib.sha256(data).hexdigest(), counts, results)


def reproduce(run: Callable[[Path], int], out_root: Path) -> dict:
    """같은 조건으로 두 번 실행하고 비교 보고를 돌려준다. `run(out_dir)`은 실행 종료 코드를 돌려준다."""
    codes, digests = [], []
    for name in ("run_a", "run_b"):
        out = out_root / name
        out.mkdir(parents=True, exist_ok=True)
        codes.append(run(out))
        digests.append(read_run(out))
    diffs = compare_runs(*digests)
    a = digests[0]
    return {
        "reproduced": not diffs, "diffs": diffs, "exit_codes": codes,
        "passed": not diffs and all(c == 0 for c in codes),
        "games": a.manifest.get("games"), "events": a.manifest.get("events"),
        "model_versions": a.manifest.get("model_versions"), "feature_version": a.manifest.get("feature_version"),
        "events_sha256": a.events_sha256,
        "winners": _winner_counts(a.results_per_game.values()),
    }


def _winner_counts(results: Iterable[dict]) -> dict:
    out: dict[str, int] = {}
    for r in results:
        out[str(r.get("winner"))] = out.get(str(r.get("winner")), 0) + 1
    return dict(sorted(out.items()))


def main(argv=None) -> int:
    """Selector로 고른 모델로 고정 시드 판을 두 번 돌려 같은지 보고한다. 같고 두 실행이 통과하면 0."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suspicion-version", required=True, help="mlruns에 있는 의심 모델 버전")
    parser.add_argument("--intent-version", required=True, help="같이 검증할 의도 기준선 버전")
    parser.add_argument("--mlruns", default="mlruns", help="로컬 Selector 루트")
    parser.add_argument("--preset", default="base6")
    parser.add_argument("--games", type=int, default=50)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", default="out/model_repro")
    parser.add_argument("--report", help="비교 보고 JSON 경로(기본 <out>/reproduce.json)")
    args = parser.parse_args(argv)
    from ml.adapters import run_model_games

    def run(out: Path) -> int:
        return run_model_games.main(["--preset", args.preset, "--games", str(args.games), "--seed", str(args.seed),
                                     "--out", str(out), "--mlruns", args.mlruns,
                                     "--suspicion-version", args.suspicion_version,
                                     "--intent-version", args.intent_version])

    out_root = Path(args.out)
    report_path = Path(args.report) if args.report else out_root / "reproduce.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)   # 이전 실행의 통과 보고가 이번 실패 뒤에 남지 않게 먼저 지운다
    try:
        report = reproduce(run, out_root)
    except (OSError, ValueError) as exc:
        report = {"passed": False, "reproduced": False, "error": type(exc).__name__, "detail": str(exc)}
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 1
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("passed", "reproduced", "exit_codes", "games", "events", "diffs")},
                     ensure_ascii=False))
    return 0 if report["passed"] else 1




if __name__ == "__main__":
    raise SystemExit(main())
