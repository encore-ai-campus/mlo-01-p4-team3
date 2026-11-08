"""사용자 테스트 지표 집계와 회차 등록용 가명 ID 추출(E-08-10·E-09-01, 표준 라이브러리만).

입력은 `server/infra/ut_export.py`가 만든 비밀 없는 JSON(판·계정)이다. 운영 DB에 직접 붙지 않는다.

계층: 도메인 `parse_time`·`scope_games`·`attempt_chains`·`phase_dwell`·`game_summary`·`aggregate`·
`select_participants`(순수 함수) / 응용·표현 `main`(하위 명령 metrics·participants, 파일 읽기·JSON 출력).

    python -m tools.ut_metrics metrics --games games.json --cutoff 2026-11-09T14:00:00+09:00 [--session-id S]
        [--exclude-pseudonym <PM 가명>] [--include-load-test] [--out result.json]
    python -m tools.ut_metrics participants --accounts accounts.json --from 2026-11-09T12:00:00+09:00
        --to 2026-11-09T12:50:00+09:00 [--exclude-pseudonym ...]

지표 정의는 `DEFINITIONS`에 버전과 함께 둔다. 문서에 없던 정의(설문 응답률 분모, 자유 입력 묶음 지표, 추궁 국면 체류 시간)는
11/8 사용자가 확정했다(`ut-metrics-1.0`).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFINITION_VERSION = "ut-metrics-1.0"
KST = timezone(timedelta(hours=9))
EXECUTED = {"confirm", "edit"}
DEFINITIONS = {
    "participants": {"text": "범위 안 판의 서로 다른 player_pseudonym 수"},
    "games_started": {"text": "범위 안 판 수(사람 판, load_test 제외, 제외 가명 제외)"},
    "completion_rate": {"text": "game_ended 시각이 마감 시각 이하이고 result.completed가 거짓이 아닌 판 ÷ 시작한 판"},
    "game_minutes": {"text": "완주 판의 game_ended − game_started 시각(분). 중앙값·p90"},
    "survey_response_rate": {"text": "skipped=false 설문 수 ÷ 끝난 판 수(game_ended가 있는 판, 마감 시각 전후 무관. 설문은 판이 끝나면 뜬다. 계획서 7절 \"응답 수 ÷ 판 수\", 건너뜀은 분모에만). 시작한 판 기준 값(response_rate_of_started)·설문 이벤트 수·건너뜀 수·설문 없는 끝난 판 수를 함께 보고"},
    "input_sentences": {"text": "intent_attempt 최초 이벤트(attempt_id별 1건, 재입력은 새 시도) 중 마스킹 후 문장이 비지 않은 수"},
    "desired_action_rate": {"text": "자유 입력 묶음(최초 시도 + superseded_by로 이어진 재입력) 중 마지막 반응이 confirm·edit(명령 수락 뒤에만 기록됨)인 묶음 ÷ 전체 묶음. 반응 없이 남은 묶음은 분모에 넣음"},
    "extra_selections": {"text": "묶음별 (재입력 수 + 마지막 반응이 edit이면 1)의 합과 실행된 묶음당 평균"},
    "cancel_rate": {"text": "마지막 반응이 cancel인 묶음 ÷ 전체 묶음"},
    "switch_to_buttons_rate": {"text": "마지막 반응이 switch_to_buttons인 묶음 ÷ 전체 묶음(none_of_these는 따로 보고)"},
    "give_up_rate": {"text": "마지막 반응이 cancel·switch_to_buttons·none_of_these인 묶음(뒤에 이어진 입력 없음) ÷ 전체 묶음. cancel_rate·switch_to_buttons_rate·none_of_these를 합친 값"},
    "interrogations_per_minute": {"text": "interrogation_answered 이벤트 수 ÷ 추궁 국면 체류 시간(분). 체류 시간은 명령 응답의 phase가 interrogation인 명령 시각부터 다음 명령 시각까지의 합(마지막이면 판 종료·마감 중 이른 시각)"},
    "lambda_30": {"text": "30 × 분당 추궁 수 / 60(건/초), E-08-10 정의. 판 전체 시간 기준 값도 함께 보고"},
}


def parse_time(value):
    """ISO 8601 문자열을 시간대 있는 시각으로. 시간대가 없으면 UTC로 본다."""
    if value is None or value == "":
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _first(events, event_type):
    return next((e for e in events if e.get("event_type") == event_type), None)


def is_load_test(game):
    """이벤트 하나라도 load_test면 부하 시험 판이다."""
    return any(e.get("load_test") for e in game.get("events", []))


def scope_games(games, session_ids=(), pseudonyms=(), exclude=(), include_load_test=False, since=None, until=None):
    """집계 범위의 판만 남긴다. 사람 판(source=human)만, 시작 시각은 [since, until]."""
    chosen = []
    for game in games:
        if game.get("source") not in ("human", None):
            continue
        if session_ids and game.get("session_id") not in session_ids:
            continue
        who = game.get("player_pseudonym")
        if pseudonyms and who not in pseudonyms:
            continue
        if who in exclude:
            continue
        if not include_load_test and is_load_test(game):
            continue
        started = started_at(game)
        if since and (started is None or started < since):
            continue
        if until and (started is None or started > until):
            continue
        chosen.append(game)
    return chosen


def started_at(game):
    """game_started 이벤트 시각, 없으면 판 메타 created_at."""
    event = _first(game.get("events", []), "game_started")
    return parse_time(event["occurred_at"]) if event else parse_time(game.get("created_at"))


def attempt_chains(events):
    """intent_attempt 이벤트를 시도별로 묶고 재입력(superseded_by)으로 이어 묶음 목록을 만든다.

    반환: 묶음마다 {"attempts": [attempt_id...], "texts": [...], "final_reaction": str|None, "retries": int}.
    같은 시도의 최초 이벤트(reaction 없음)와 반응 이벤트를 합친다. 같은 event_id는 한 번만 센다.
    """
    seen, attempts, order = set(), {}, []
    for e in events:
        if e.get("event_type") != "intent_attempt" or e.get("event_id") in seen:
            continue
        seen.add(e.get("event_id"))
        p = e.get("payload") or {}
        aid = p.get("attempt_id")
        if aid is None:
            continue
        if aid not in attempts:
            attempts[aid] = {"text": p.get("text") or "", "reaction": None, "superseded_by": None}
            order.append(aid)
        if p.get("reaction") is not None:
            attempts[aid]["reaction"] = p["reaction"]
            attempts[aid]["superseded_by"] = p.get("superseded_by")
    successors = {a["superseded_by"] for a in attempts.values() if a["superseded_by"]}
    chains = []
    for aid in order:
        if aid in successors:
            continue  # 재입력으로 생긴 시도는 앞 묶음에 붙는다
        chain, current = [], aid
        while current in attempts and current not in chain:
            chain.append(current)
            current = attempts[current]["superseded_by"]
        last = attempts[chain[-1]]
        chains.append({"attempts": chain, "texts": [attempts[a]["text"] for a in chain],
                       "final_reaction": last["reaction"], "retries": len(chain) - 1})
    return chains, attempts


def _command_first_seen(game):
    """명령 ID → 그 명령 이벤트의 가장 이른 occurred_at."""
    first_seen = {}
    for e in game.get("events", []):
        cid = e.get("command_id")
        moment = parse_time(e.get("occurred_at"))
        if cid and moment and (cid not in first_seen or moment < first_seen[cid]):
            first_seen[cid] = moment
    return first_seen


def command_times(game):
    """수락된 명령마다 (시각, 명령 뒤 phase). 시각은 그 명령 이벤트의 가장 이른 occurred_at."""
    first_seen = _command_first_seen(game)
    rows = []
    for c in game.get("commands", []):
        if c.get("replayed") or c.get("command_id") not in first_seen:
            continue
        rows.append((first_seen[c["command_id"]], c.get("phase")))
    rows.sort(key=lambda r: r[0])
    return rows


def phase_dwell(game, phase, end):
    """명령 응답 phase가 `phase`인 구간의 체류 초. 마지막 구간은 `end`까지, 모든 구간은 `end`에서 자른다."""
    rows = command_times(game)
    total = 0.0
    for i, (moment, after) in enumerate(rows):
        if end is not None and moment >= end:
            break
        if after != phase:
            continue
        nxt = rows[i + 1][0] if i + 1 < len(rows) else end
        if nxt is not None and end is not None:
            nxt = min(nxt, end)
        if nxt is not None and nxt > moment:
            total += (nxt - moment).total_seconds()
    return total


def game_summary(game, cutoff=None):
    """판 하나의 집계 재료. 마감 시각 뒤에 끝난 판은 완주로 세지 않는다.

    마감 시각을 주면 그 뒤의 입력·반응·추궁·명령은 세지 않는다. 설문은 판이 끝나면 뜨므로 시각과 관계없이 둔다.
    """
    all_events = game.get("events", [])
    events = [e for e in all_events if cutoff is None or e.get("event_type") == "survey_submitted"
              or (parse_time(e.get("occurred_at")) or cutoff) <= cutoff]
    start = started_at(game)
    ended_event = _first(all_events, "game_ended")  # 종료 시각은 마감 뒤여도 읽고 완주 판정에서 거른다
    end = parse_time(ended_event["occurred_at"]) if ended_event else None
    result = game.get("result") or {}
    completed = end is not None and result.get("completed", True) is not False and (cutoff is None or end <= cutoff)
    surveys = [e for e in events if e.get("event_type") == "survey_submitted"]
    survey = (surveys[-1].get("payload") or {}) if surveys else None
    chains, attempts = attempt_chains(events)
    first_seen = _command_first_seen(game)
    stop = min(t for t in (end, cutoff) if t is not None) if (end or cutoff) else None
    last_event = max((parse_time(e.get("occurred_at")) for e in all_events if e.get("occurred_at")), default=None)
    play_end = stop if stop is not None else last_event
    if play_end is not None and last_event is not None and end is None:
        play_end = min(play_end, last_event)  # 끝나지 않은 판은 마지막 기록까지만
    return {
        "game_id": game.get("game_id"), "player_pseudonym": game.get("player_pseudonym"),
        "started_at": start, "ended_at": end, "completed": completed,
        "minutes": (end - start).total_seconds() / 60 if completed and start else None,
        "play_seconds": (play_end - start).total_seconds() if play_end and start and play_end > start else 0.0,
        "survey": survey, "chains": chains,
        "input_sentences": sum(1 for a in attempts.values() if a["text"].strip()),
        "interrogations": sum(1 for e in events if e.get("event_type") == "interrogation_answered"),
        "interrogation_seconds": phase_dwell(game, "interrogation", play_end),
        "accepted_commands": sum(1 for c in game.get("commands", []) if c.get("accepted") and not c.get("replayed")
                                 and (cutoff is None or (c.get("command_id") in first_seen and first_seen[c["command_id"]] <= cutoff))),
    }


def _rate(n, d):
    return round(n / d, 4) if d else None


def _pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))]


def aggregate(summaries):
    """판 집계 재료 목록 → 지표 표. 값마다 분자·분모를 함께 남긴다."""
    started = len(summaries)
    done = [s for s in summaries if s["completed"]]
    minutes = [s["minutes"] for s in done if s["minutes"] is not None]
    ended = [s for s in summaries if s["ended_at"] is not None]  # 마감 뒤에 끝난 판도 설문이 뜬다
    surveys = [s["survey"] for s in ended if s["survey"] is not None]
    answered = sum(1 for p in surveys if p.get("skipped") is False)
    chains = [c for s in summaries for c in s["chains"]]
    finals = [c["final_reaction"] for c in chains]
    executed = [c for c in chains if c["final_reaction"] in EXECUTED]
    extra = sum(c["retries"] + (1 if c["final_reaction"] == "edit" else 0) for c in chains)
    interrogations = sum(s["interrogations"] for s in summaries)
    dwell_min = sum(s["interrogation_seconds"] for s in summaries) / 60
    play_min = sum(s["play_seconds"] for s in summaries) / 60
    per_phase = interrogations / dwell_min if dwell_min else None
    per_play = interrogations / play_min if play_min else None
    by_player = {}
    for s in summaries:
        by_player.setdefault(s["player_pseudonym"], []).append(s)
    person_minutes = []
    for rows in by_player.values():
        starts = [r["started_at"] for r in rows if r["started_at"]]
        ends = [r["ended_at"] for r in rows if r["completed"] and r["ended_at"]]  # 마감 뒤 종료는 빼고
        if starts and ends and max(ends) > min(starts):
            person_minutes.append((max(ends) - min(starts)).total_seconds() / 60)
    return {
        "participants": len(by_player),
        "games_started": started,
        "games_completed": len(done),
        "completion_rate": _rate(len(done), started),
        "game_minutes": {"median": round(statistics.median(minutes), 2) if minutes else None,
                         "p90": round(_pct(minutes, 0.9), 2) if minutes else None, "n": len(minutes)},
        "person_total_minutes": {"median": round(statistics.median(person_minutes), 2) if person_minutes else None,
                                 "n": len(person_minutes)},
        "games_per_participant": round(started / len(by_player), 2) if by_player else None,
        "survey": {"events": len(surveys), "answered": answered, "skipped": len(surveys) - answered,
                   "games_ended": len(ended), "ended_without_survey": len(ended) - len(surveys),
                   "response_rate": _rate(answered, len(ended)), "response_rate_of_started": _rate(answered, started)},
        "input_sentences": sum(s["input_sentences"] for s in summaries),
        "free_input": {"chains": len(chains), "executed": len(executed),
                       "desired_action_rate": _rate(len(executed), len(chains)),
                       "extra_selections": extra,
                       "extra_selections_per_executed": _rate(extra, len(executed)),
                       "cancel_rate": _rate(finals.count("cancel"), len(chains)),
                       "switch_to_buttons_rate": _rate(finals.count("switch_to_buttons"), len(chains)),
                       "none_of_these": finals.count("none_of_these"),
                       "give_up_rate": _rate(sum(finals.count(k) for k in ("cancel", "switch_to_buttons", "none_of_these")),
                                             len(chains)),
                       "unresolved": finals.count(None)},
        "accepted_commands": sum(s["accepted_commands"] for s in summaries),
        "interrogations": {"count": interrogations, "phase_minutes": round(dwell_min, 3),
                           "per_minute_phase": round(per_phase, 4) if per_phase is not None else None,
                           "per_minute_play": round(per_play, 4) if per_play is not None else None,
                           "lambda_30_phase": round(30 * per_phase / 60, 4) if per_phase is not None else None,
                           "lambda_30_play": round(30 * per_play / 60, 4) if per_play is not None else None},
    }


def select_participants(accounts, start, end, exclude=()):
    """가입 시각이 [start, end](경계 포함)인 동의·활성·비운영자 계정의 가명 ID(가입 순)."""
    chosen = []
    for a in accounts:
        joined = parse_time(a.get("date_joined"))
        if joined is None or joined < start or joined > end:
            continue
        if a.get("is_staff") or not a.get("is_active", True) or not a.get("consented_at"):
            continue
        if a["player_pseudonym"] in exclude:
            continue
        chosen.append(a["player_pseudonym"])
    return chosen


def _kst(value):
    """명령줄 시각. 시간대가 없으면 KST로 본다(판 기록의 시각은 UTC로 저장돼 있다)."""
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=KST)


def _utf8_stdout():
    """Windows 콘솔(cp949)에서도 정의 문장(예: '−')을 출력할 수 있게 한다."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    """metrics: 판 내보내기 → 지표 JSON. participants: 계정 내보내기 → 회차 등록용 가명 ID."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("metrics")
    m.add_argument("--games", required=True)
    m.add_argument("--cutoff", help="마감 시각(시간대 없으면 KST). 뒤에 끝난 판은 미완주")
    m.add_argument("--since")
    m.add_argument("--until", help="판 시작 시각 상한(기본: --cutoff)")
    m.add_argument("--session-id", action="append", default=[])
    m.add_argument("--pseudonym", action="append", default=[], help="이 가명만")
    m.add_argument("--exclude-pseudonym", action="append", default=[], help="PM 등 제외할 가명")
    m.add_argument("--include-load-test", action="store_true")
    m.add_argument("--per-game", action="store_true", help="판별 요약도 출력")
    m.add_argument("--out")
    p = sub.add_parser("participants")
    p.add_argument("--accounts", required=True)
    p.add_argument("--from", dest="start", required=True, help="가입 구간 시작(포함, 시간대 없으면 KST)")
    p.add_argument("--to", dest="end", required=True, help="가입 구간 끝(포함)")
    p.add_argument("--exclude-pseudonym", action="append", default=[])
    args = parser.parse_args(argv)
    _utf8_stdout()
    if args.cmd == "metrics":
        doc = _load(args.games)
        cutoff = _kst(args.cutoff) if args.cutoff else None
        until = _kst(args.until) if args.until else cutoff  # 마감 뒤 시작한 판은 기본으로 뺀다
        games = scope_games(doc.get("games", []), set(args.session_id), set(args.pseudonym),
                            set(args.exclude_pseudonym), args.include_load_test,
                            _kst(args.since) if args.since else None, until)
        summaries = [game_summary(g, cutoff) for g in games]
        result = {"definition_version": DEFINITION_VERSION, "export_version": doc.get("export_version"),
                  "exported_at": doc.get("exported_at"), "cutoff": cutoff.isoformat() if cutoff else None,
                  "filters": {"session_ids": sorted(args.session_id), "excluded": len(args.exclude_pseudonym),
                              "include_load_test": args.include_load_test},
                  "metrics": aggregate(summaries),
                  "definitions": DEFINITIONS}
        if args.per_game:
            result["games"] = [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in s.items()
                                if k not in ("chains", "survey")} for s in summaries]
        text = json.dumps(result, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(text)
    else:
        doc = _load(args.accounts)
        chosen = select_participants(doc.get("accounts", []), _kst(args.start), _kst(args.end),
                                     set(args.exclude_pseudonym))
        print(json.dumps({"count": len(chosen), "from": _kst(args.start).isoformat(), "to": _kst(args.end).isoformat()},
                         ensure_ascii=False), file=sys.stderr)
        print(" ".join(chosen))


if __name__ == "__main__":
    main()
