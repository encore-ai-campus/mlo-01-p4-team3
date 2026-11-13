"""A-06-03 인수 패키지 검사: `tools.run_engine_games` 출력이 완료 조건 1과 11/6 A → D 인수 조건 4개를 만족하는지.

    python -m tools.check_handoff_a06 --dir out/handoff/a06
    python -m tools.check_handoff_a06 --dir out/handoff/a06 --record A-06-03

검사 항목(`parallel_dev.md` 3절, A_engine.md A-06-03):
- 조건 1: 판마다 `game_ended` 정확히 1번, `seq`가 0부터 빈틈 없음, 모든 줄이 `serde` 엄격 검증 통과
  (봉투는 `EventEnvelope`, `observation` 본문의 스냅샷은 `ObservationSnapshot`), 판 수 = manifest 판 수
- 인수 1 기본 6인 판 완주: 모든 판이 `base6`, 주민(봉투 `npc_policy_versions`) 6명, 마지막 단계가 `ended`
- 인수 2 행동 직전 관측 스냅샷: `decision`마다 바로 앞 이벤트가 같은 `snapshot_id`·관찰자의 `observation`,
  판마다 observation 수 = decision 수
- 인수 3 종료 후 진실: 판마다 `truth_revealed` 정확히 1번, `game_ended` 바로 뒤 마지막 이벤트, `paldo.truth` 토픽,
  주민 전원과 무당의 역할이 들어 있음
- 인수 4 결정 이유 코드: 판마다 `decision` 1개 이상, `decision`마다 이유 코드 1개 이상, 모두 `ReasonCode` 값, `policy_version`이
  봉투 `npc_policy_versions[actor]`와 같음

실패가 하나라도 있으면 종료 코드 1. 위반은 조건별로 최대 20건까지 로그에 싣는다.
계층: 표현·응용(파일 읽기·출력)과 검사 함수(`check_lines`, 입출력 없음). 엔진 규칙은 바꾸지 않는다.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from engine.domain import serde
from engine.domain.events import EventEnvelope, EventType, topic_of
from engine.domain.model import PLAYER_ID, PRESETS, ObservationSnapshot, Phase, Role
from engine.domain.observation import ReasonCode
from tools.evidence_result import write_result
from tools.jsonlog import log

COMPONENT = "check_handoff_a06"
CONDITIONS = ("c1_schema", "c1_seq", "c1_ended_once", "c1_game_count",
              "h1_base6_completed", "h2_observation_before_decision", "h3_truth_after_end", "h4_reason_codes")
OBS_KEYS = {"snapshot_id", "observer", "snapshot"}
DECISION_KEYS = {"actor", "snapshot_id", "kind", "target", "reason_codes", "evidence", "policy_version"}
TRUTH_KEYS = {"roles", "secrets", "lies", "whereabouts", "night_plans"}
MAX_EXAMPLES = 20
_REASONS = {r.value for r in ReasonCode}
_ROLES = {r.value for r in Role}


@dataclass
class Report:
    """검사 결과. `violations[조건]`은 위반 설명 목록, 비어 있으면 통과."""
    games: int = 0
    events: int = 0
    observations: int = 0
    decisions: int = 0
    truths: int = 0
    violations: dict[str, list[str]] = field(default_factory=lambda: {c: [] for c in CONDITIONS})

    def add(self, cond: str, msg: str) -> None:
        """위반 1건을 기록한다."""
        self.violations[cond].append(msg)

    @property
    def ok(self) -> bool:
        """모든 조건에 위반이 없으면 True."""
        return not any(self.violations.values())


def _parse(line: str, n: int, rep: Report) -> Optional[EventEnvelope]:
    """한 줄을 엄격 검증해 봉투로 바꾼다. 실패하면 c1_schema 위반을 남기고 None."""
    try:
        env = serde.from_jsonable(EventEnvelope, json.loads(line))
    except (ValueError, serde.SchemaError) as e:
        rep.add("c1_schema", f"줄 {n}: {e}")
        return None
    try:
        et = EventType(env.event_type)
    except ValueError:
        rep.add("c1_schema", f"줄 {n}: 모르는 event_type {env.event_type!r}")
        return env
    if env.topic != topic_of(et):
        rep.add("c1_schema", f"줄 {n}: {et.value} 토픽 {env.topic!r} != {topic_of(et)!r}")
    try:
        Phase(env.phase)
    except ValueError:
        rep.add("c1_schema", f"줄 {n}: 모르는 phase {env.phase!r}")
    if et is EventType.OBSERVATION:
        p = env.payload
        if set(p) != OBS_KEYS:
            rep.add("c1_schema", f"줄 {n}: observation 본문 키 {sorted(p)}")
        else:
            try:
                snap = serde.from_jsonable(ObservationSnapshot, p["snapshot"], f"줄 {n}.payload.snapshot")
            except serde.SchemaError as e:
                rep.add("c1_schema", str(e))
            else:
                if snap.snapshot_id != p["snapshot_id"] or snap.observer != p["observer"]:
                    rep.add("c1_schema", f"줄 {n}: 스냅샷 ID·관찰자가 본문과 다름")
    return env


def _truth_body_problems(p: dict, residents: set[str]) -> list[str]:
    """진실 본문의 역할 밖 필드 형식: 비밀 배정 {주민: 비밀 ID}, 거짓 주장 ID 목록, 밤별 행적·밤 명령 객체(비어 있지 않음)."""
    out = []
    sec = p["secrets"]
    if (not isinstance(sec, dict) or not set(sec) <= residents
            or not all(isinstance(v, str) and v for v in sec.values())):
        out.append("진실 secrets가 {주민: 비밀 ID} 형식이 아님")
    if not isinstance(p["lies"], list) or not all(isinstance(c, str) for c in p["lies"]):
        out.append("진실 lies가 주장 ID 목록이 아님")
    for k in ("whereabouts", "night_plans"):
        if not isinstance(p[k], dict) or not p[k] or not all(isinstance(v, dict) for v in p[k].values()):
            out.append(f"진실 {k}가 밤별 객체가 아님")
    return out


def _check_game(gid: str, evs: list[EventEnvelope], rep: Report) -> None:
    """판 하나의 순번·종료·진실·관측-결정 짝·이유 코드를 검사한다."""
    seqs = [e.seq for e in evs]
    if seqs != list(range(len(evs))):
        rep.add("c1_seq", f"{gid}: seq가 0..{len(evs) - 1} 연속이 아님")
    if len({e.event_id for e in evs}) != len(evs):
        rep.add("c1_seq", f"{gid}: event_id 중복")
    types = [e.event_type for e in evs]
    n_end = types.count(EventType.GAME_ENDED.value)
    if n_end != 1:
        rep.add("c1_ended_once", f"{gid}: game_ended {n_end}번")
    if any(e.preset_id != "base6" for e in evs):
        rep.add("h1_base6_completed", f"{gid}: base6가 아닌 이벤트")
    if evs and len(evs[0].npc_policy_versions) != PRESETS["base6"].n_active:
        rep.add("h1_base6_completed", f"{gid}: 주민 {len(evs[0].npc_policy_versions)}명(기대 {PRESETS['base6'].n_active})")
    if not evs or evs[-1].phase != Phase.ENDED.value:
        rep.add("h1_base6_completed", f"{gid}: 마지막 단계가 ended가 아님")

    n_truth = types.count(EventType.TRUTH_REVEALED.value)
    rep.truths += n_truth
    if n_truth != 1 or types[-1] != EventType.TRUTH_REVEALED.value or types[-2:-1] != [EventType.GAME_ENDED.value]:
        rep.add("h3_truth_after_end", f"{gid}: truth_revealed {n_truth}번, 끝 순서 {types[-2:]}")
    else:
        t = evs[-1]
        roles = t.payload.get("roles", {})
        residents = set(t.npc_policy_versions)
        if set(t.payload) != TRUTH_KEYS:
            rep.add("h3_truth_after_end", f"{gid}: 진실 본문 키 {sorted(t.payload)}")
        elif (not isinstance(roles, dict) or not all(isinstance(v, str) for v in roles.values())
              or set(roles) != residents | {PLAYER_ID} or not set(roles.values()) <= _ROLES):
            rep.add("h3_truth_after_end", f"{gid}: 진실 역할 대상·값이 주민 전원+무당과 다름")
        else:
            for msg in _truth_body_problems(t.payload, residents):
                rep.add("h3_truth_after_end", f"{gid}: {msg}")
        if t.phase != Phase.ENDED.value or t.topic != topic_of(EventType.TRUTH_REVEALED):
            rep.add("h3_truth_after_end", f"{gid}: 진실 이벤트 단계 {t.phase}, 토픽 {t.topic}")

    n_obs = types.count(EventType.OBSERVATION.value)
    n_dec = types.count(EventType.DECISION.value)
    if n_dec == 0:
        rep.add("h4_reason_codes", f"{gid}: decision이 없음")
    if n_obs != n_dec:
        rep.add("h2_observation_before_decision", f"{gid}: observation {n_obs}개 != decision {n_dec}개")
    for i, e in enumerate(evs):
        if e.event_type == EventType.OBSERVATION.value:
            rep.observations += 1
        if e.event_type != EventType.DECISION.value:
            continue
        rep.decisions += 1
        p = e.payload
        if set(p) != DECISION_KEYS:
            rep.add("h4_reason_codes", f"{gid}#{e.seq}: decision 본문 키 {sorted(p)}")
            continue
        if not isinstance(p["actor"], str) or not isinstance(p["snapshot_id"], str):
            rep.add("h4_reason_codes", f"{gid}#{e.seq}: actor·snapshot_id가 문자열이 아님")
            continue
        prev = evs[i - 1] if i > 0 else None
        if (prev is None or prev.event_type != EventType.OBSERVATION.value
                or prev.payload.get("snapshot_id") != p["snapshot_id"] or prev.payload.get("observer") != p["actor"]):
            rep.add("h2_observation_before_decision", f"{gid}#{e.seq}: 바로 앞에 같은 snapshot_id의 observation 없음")
        codes = p["reason_codes"]
        if (not isinstance(codes, list) or not codes or not all(isinstance(c, str) for c in codes)
                or not set(codes) <= _REASONS):
            rep.add("h4_reason_codes", f"{gid}#{e.seq}: 이유 코드 {codes!r}")
        expected = e.npc_policy_versions.get(p["actor"])
        if not isinstance(p["policy_version"], str) or not p["policy_version"] or p["policy_version"] != expected:
            rep.add("h4_reason_codes", f"{gid}#{e.seq}: policy_version {p['policy_version']!r} != 봉투 {expected!r}")


def check_lines(lines: Iterable[str], expected_games: Optional[int] = None) -> Report:
    """jsonl 줄들을 검사해 `Report`를 돌려준다. 판은 줄에 처음 나온 순서로 모으고, 판 안 순서는 줄 순서 그대로 본다."""
    rep = Report()
    games: dict[str, list[EventEnvelope]] = defaultdict(list)
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        rep.events += 1
        env = _parse(line, n, rep)
        if env is not None:
            games[env.game_id].append(env)
    rep.games = len(games)
    if expected_games is not None and rep.games != expected_games:
        rep.add("c1_game_count", f"판 수 {rep.games} != manifest {expected_games}")
    if rep.games == 0:
        rep.add("c1_game_count", "이벤트가 없음")
    for gid, evs in games.items():
        _check_game(gid, evs, rep)
    return rep


def metrics_of(rep: Report) -> dict:
    """결과 JSON 지표: 개수와 조건별 위반 수."""
    m = {
        "games": {"value": rep.games, "unit": "판"},
        "events": {"value": rep.events, "unit": "개"},
        "observations": {"value": rep.observations, "unit": "개"},
        "decisions": {"value": rep.decisions, "unit": "개"},
        "truths": {"value": rep.truths, "unit": "개"},
    }
    for c in CONDITIONS:
        m[f"violations_{c}"] = {"value": len(rep.violations[c]), "unit": "건"}
    return m


def main(argv=None) -> int:
    """명령행 진입점. 통과면 0, 위반이 있으면 1."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", default="out/handoff/a06")
    ap.add_argument("--record", metavar="TASK_ID", help="결과 JSON을 docs/evidence/perf/<TASK_ID>/handoff_check.json에 쓴다")
    a = ap.parse_args(argv)
    d = Path(a.dir)
    events_path, manifest_path = d / "events.jsonl", d / "manifest.json"
    if not events_path.is_file() or not manifest_path.is_file():
        log(COMPONENT, "error", "입력 없음", error_type="FileNotFoundError", dir=str(d))
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    games = manifest.get("games") if isinstance(manifest, dict) else None
    if isinstance(games, bool) or not isinstance(games, int) or games <= 0:
        log(COMPONENT, "error", "manifest 판 수 없음", error_type="ManifestError", dir=str(d), games=repr(games))
        return 1
    log(COMPONENT, "info", "검사 시작", dir=str(d), command=manifest.get("command"))
    with open(events_path, encoding="utf-8") as f:
        rep = check_lines(f, games)
    metrics = metrics_of(rep)
    for c in CONDITIONS:
        v = rep.violations[c]
        log(COMPONENT, "info" if not v else "error", "조건 결과", condition=c, violations=len(v),
            **({"error_type": "HandoffViolation", "examples": v[:MAX_EXAMPLES]} if v else {}))
    log(COMPONENT, "info" if rep.ok else "error", "검사 완료", ok=rep.ok,
        **{k: v["value"] for k, v in metrics.items() if not k.startswith("violations_")})
    if a.record:
        source = "python -m tools.check_handoff_a06" + (f" --dir {a.dir}" if a.dir != "out/handoff/a06" else "")
        path = write_result(a.record, "handoff_check", metrics, source)
        log(COMPONENT, "info", "결과 JSON 기록", path=str(path))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
