"""사용자 테스트 집계용 읽기 전용 내보내기(인프라, E-08-10·E-09-01·E-09-02).

게임 웹 Mongo(`games`)의 판 메타·결과·아웃박스 이벤트·명령 응답 요지와, 계정 DB의 가명 ID·가입 시각을
비밀 없는 JSON으로 내보낸다. 계산은 `tools/ut_metrics.py`·`tools/ut_freeze.py`(표준 라이브러리)가 한다.
`tools/`는 제3자 라이브러리를 import하지 않으므로(`tests/contract/test_layers.py`) pymongo·Django 부분을 여기 둔다.

쓰기 없음: `find`만 호출하고 인덱스 생성·임대 기록을 하는 기존 저장소 어댑터(`MongoSessionStore`,
`MongoInteractionStore`)는 쓰지 않는다. 접속 주소·계정은 실행 환경 변수(`PALDO_MONGO_URI`, Django 계정 DB 설정)로만 받는다.

    python -m server.infra.ut_export games --out games.json [--session-id S ...] [--prefix P]
    DJANGO_SETTINGS_MODULE=<설정> python -m server.infra.ut_export accounts --out accounts.json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

EXPORT_VERSION = "ut-export-1"
# 판 문서에서 읽는 필드만. 진실(`truth`)과 NPC 내부 상태는 읽지 않는다.
GAME_PROJECTION = {"state.meta": 1, "state.result": 1, "state.phase": 1, "state.day": 1,
                   "state.state_version": 1, "outbox.envelope": 1, "outbox.committed_at": 1,
                   "commands.command_id": 1, "commands.response_json": 1}
ENVELOPE_FIELDS = ("event_id", "seq", "state_version", "event_type", "occurred_at", "day", "phase", "source",
                   "payload", "player_pseudonym", "session_id", "command_id", "load_test")
# 플레이어 화면이 아닌 학습·진실용 본문은 내보내지 않는다(집계에 쓰지 않음).
SKIP_EVENT_TYPES = {"truth_revealed", "observation", "decision"}


def _iso(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return value


def _command_summary(row, first_event_at=None):
    """명령 응답 JSON에서 시각 계산에 필요한 요지만 남긴다(화면 본문은 버림).

    `first_event_at`: 그 명령이 낸 아웃박스 이벤트(내보내지 않는 종류 포함)의 가장 이른 `occurred_at`.
    추궁 → 투표 전환처럼 `observation`·`decision`만 내는 명령도 국면 체류 시간 계산에 시각이 필요하다.
    """
    try:
        response = json.loads(row.get("response_json") or "{}")
    except ValueError:
        response = {}
    view = response.get("view") or {}
    return {"command_id": row.get("command_id"), "accepted": bool(response.get("accepted")),
            "replayed": bool(response.get("replayed")), "state_version": view.get("state_version"),
            "day": view.get("day"), "phase": view.get("phase"), "reject": response.get("reject"),
            "first_event_at": first_event_at}


def game_record(document):
    """Mongo 판 문서 하나를 내보내기 행으로 바꾼다(순수 변환, 시험 대상)."""
    state = document.get("state") or {}
    meta = state.get("meta") or {}
    events = []
    first_seen = {}
    for row in document.get("outbox") or []:
        envelope = row.get("envelope") or {}
        cid, moment = envelope.get("command_id"), _iso(envelope.get("occurred_at"))
        if cid and moment and (cid not in first_seen or moment < first_seen[cid]):
            first_seen[cid] = moment  # 시각만(본문 없음). 같은 형식의 UTC ISO 문자열이라 문자열 비교로 충분
        if envelope.get("event_type") in SKIP_EVENT_TYPES:
            continue
        item = {name: envelope.get(name) for name in ENVELOPE_FIELDS}
        item["committed_at"] = _iso(row.get("committed_at"))
        events.append(item)
    events.sort(key=lambda e: (e["seq"] is None, e["seq"]))
    preset = meta.get("preset")
    return {"game_id": document.get("_id"), "player_pseudonym": meta.get("player_pseudonym"),
            "session_id": meta.get("session_id"), "source": meta.get("source"),
            "preset_id": preset.get("preset_id") if isinstance(preset, dict) else preset,
            "seed": meta.get("seed"), "created_at": meta.get("created_at"),
            "rule_version": meta.get("rule_version"), "content_version": meta.get("content_version"),
            "phase": state.get("phase"), "day": state.get("day"), "result": state.get("result"),
            "commands": [_command_summary(c, first_seen.get(c.get("command_id")))
                         for c in document.get("commands") or []], "events": events}


def export_games(collection, session_ids=None):
    """`find`만 써서 판 문서를 읽는다. 회차 ID를 주면 그 회차의 판만."""
    query = {"state.meta.session_id": {"$in": list(session_ids)}} if session_ids else {}
    rows = [game_record(d) for d in collection.find(query, GAME_PROJECTION)]
    rows.sort(key=lambda r: (r["created_at"] or "", r["game_id"] or ""))
    return {"export_version": EXPORT_VERSION, "kind": "games", "exported_at": _iso(datetime.now(timezone.utc)),
            "session_ids": sorted(session_ids or []), "games": rows}


def account_record(pseudonym, date_joined, consented_at, is_staff, is_active):
    """계정 한 건을 내보내기 행으로(로그인 ID·이름·비밀번호는 넣지 않는다)."""
    return {"player_pseudonym": str(pseudonym), "date_joined": _iso(date_joined),
            "consented_at": _iso(consented_at), "is_staff": bool(is_staff), "is_active": bool(is_active)}


def export_accounts(rows):
    """(가명, 가입 시각, 동의 시각, 운영자 여부, 활성 여부) 목록을 내보내기 문서로."""
    accounts = sorted((account_record(*r) for r in rows), key=lambda a: (a["date_joined"] or "", a["player_pseudonym"]))
    return {"export_version": EXPORT_VERSION, "kind": "accounts", "exported_at": _iso(datetime.now(timezone.utc)),
            "accounts": accounts}


def _games_collection(prefix):
    from pymongo import MongoClient
    from pymongo.read_concern import ReadConcern
    uri = os.environ.get("PALDO_MONGO_URI", "")
    if not uri:
        raise SystemExit("PALDO_MONGO_URI not set")
    client = MongoClient(uri, timeoutMS=15000, serverSelectionTimeoutMS=5000)
    database = client.get_default_database("paldo_web", read_concern=ReadConcern("majority"))
    return client, database[prefix + "games"]


def _account_rows():
    import django
    django.setup()
    from server.accounts.models import AccountProfile
    for p in AccountProfile.objects.select_related("user").order_by("user__date_joined"):
        yield p.player_pseudonym, p.user.date_joined, p.consented_at, p.user.is_staff, p.user.is_active


def main(argv=None):
    """하위 명령 games·accounts. 결과 파일만 쓰고 표준 출력에는 건수만 남긴다."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="kind", required=True)
    games = sub.add_parser("games")
    games.add_argument("--out", type=Path, required=True)
    games.add_argument("--session-id", action="append", default=[])
    games.add_argument("--prefix", default="", help="시험용 컬렉션 접두어(운영은 비움)")
    accounts = sub.add_parser("accounts")
    accounts.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.kind == "games":
        client, collection = _games_collection(args.prefix)
        try:
            document = export_games(collection, args.session_id)
        finally:
            client.close()
        count = len(document["games"])
    else:
        document = export_accounts(_account_rows())
        count = len(document["accounts"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"kind": args.kind, "rows": count, "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
