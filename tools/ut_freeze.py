"""`test_human` 분할·교집합 검사·동결 묶음 만들기(E-09-02, 표준 라이브러리만).

입력은 `server/infra/ut_export.py games`의 JSON(정식 테스트, 사전 테스트)과 학습·개발 세트 파일, 개인정보 검수 결과다.

계층: 도메인 `normalize`·`sentences_of`·`split_users`·`build_freeze`(순수 함수) / 응용·표현 `main`(파일 읽기·쓰기).

    python -m tools.ut_freeze --games usertest.json --pretest-games pretest.json --pm-pseudonym <PM 가명>
        --seed 20261109 --exclude-set train.jsonl --exclude-set dev.jsonl --privacy-review review.json
        [--session-id S] --out-dir <동결 폴더>

규칙(정의 버전 `FREEZE_VERSION`):
- 문장 = `intent_attempt` 최초 이벤트의 마스킹된 문장(시도별 1건, 빈 문장 제외). 정규화 `normalize`(NFC, 앞뒤 공백 제거, 연속 공백 1칸).
- 사용자 분할: PM을 뺀 정식 테스트 참가자 가명을 정렬하고 `random.Random(seed)`로 섞어 앞 floor(n/2)명을 평가(test_human)로.
  홀수면 평가 쪽이 1명 적다(11/8 사용자 확정).
- test_human = 사전 테스트 PM 문장(`pm: true`) + 평가 사용자 문장 − 개인정보 검수 제외.
- 검사: 평가·학습 사용자 교집합 0, 평가 문장과 학습 후보·외부 세트 문장의 정규화 교집합 0. 충돌이 있으면 실패(`--drop-conflicts`면 평가에서 빼고 수를 보고).
- 동결 기록: Delta 버전 대신 동결 폴더의 `manifest.json`(파일별 바이트 수·sha256)을 쓴다(11/8 사용자 결정, 집계는 Mongo·계정 DB 직접).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
import unicodedata
from pathlib import Path

FREEZE_VERSION = "ut-freeze-1.0"
NORMALIZE_VERSION = "norm-1"


def normalize(text):
    """비교용 정규화: NFC, 앞뒤 공백 제거, 연속 공백 1칸. 대소문자·문장부호는 바꾸지 않는다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def sentences_of(games, pm_pseudonyms=(), session_ids=()):
    """판 내보내기에서 시도별 최초 문장을 뽑는다. 같은 event_id는 한 번만."""
    rows, seen = [], set()
    for game in games:
        if session_ids and game.get("session_id") not in session_ids:
            continue
        if game.get("source") not in ("human", None):
            continue
        if any(e.get("load_test") for e in game.get("events", [])):
            continue
        for e in game.get("events", []):
            if e.get("event_type") != "intent_attempt" or e.get("event_id") in seen:
                continue
            seen.add(e.get("event_id"))
            p = e.get("payload") or {}
            if p.get("reaction") is not None or not normalize(p.get("text")):
                continue  # 반응 이벤트는 같은 시도의 후속 기록
            who = e.get("player_pseudonym") or game.get("player_pseudonym")
            rows.append({"attempt_id": p.get("attempt_id"), "game_id": game.get("game_id"),
                         "player_pseudonym": who, "pm": who in pm_pseudonyms, "text": p.get("text"),
                         "mask_version": p.get("mask_version"), "occurred_at": e.get("occurred_at")})
    return rows


def players_of(games, pm_pseudonyms=(), session_ids=()):
    """분할 대상 참가자: 범위 안 사람 판을 한 판이라도 한 가명(문장이 없어도 포함, PM 제외)."""
    return sorted({g.get("player_pseudonym") for g in games
                   if g.get("player_pseudonym") and g.get("player_pseudonym") not in pm_pseudonyms
                   and (not session_ids or g.get("session_id") in session_ids)
                   and g.get("source") in ("human", None)
                   and not any(e.get("load_test") for e in g.get("events", []))})


def split_users(users, seed):
    """정렬한 가명 목록을 시드로 섞어 (평가, 학습)으로 나눈다. 평가는 floor(n/2)명."""
    ordered = sorted(set(users))
    shuffled = list(ordered)
    random.Random(seed).shuffle(shuffled)
    cut = len(shuffled) // 2
    return sorted(shuffled[:cut]), sorted(shuffled[cut:]), ordered


def build_freeze(usertest, pretest, seed, external=(), privacy_excluded=(), drop_conflicts=False, players=None):
    """동결 묶음과 검사 결과를 만든다.

    usertest·pretest: `sentences_of` 결과. external: 학습·개발 세트 행({"text", "user"?}).
    privacy_excluded: 개인정보 검수로 뺄 attempt_id → 사유. players: 분할 대상 참가자(`players_of`, 없으면 문장 작성자).
    """
    participants = list(players) if players is not None else [
        r["player_pseudonym"] for r in usertest if not r["pm"] and r["player_pseudonym"]]
    eval_users, train_users, ordered = split_users(participants, seed)
    eval_set = set(eval_users)
    pm_rows = [dict(r, pm=True) for r in pretest]
    eval_rows = [r for r in usertest if r["player_pseudonym"] in eval_set and not r["pm"]]
    train_all = [r for r in usertest if r["player_pseudonym"] in set(train_users) and not r["pm"]]
    train_rows = [r for r in train_all if r["attempt_id"] not in privacy_excluded]  # 학습 쪽에도 검수 제외 적용
    excluded = [r for r in pm_rows + eval_rows if r["attempt_id"] in privacy_excluded]
    test_rows = [r for r in pm_rows + eval_rows if r["attempt_id"] not in privacy_excluded]
    other_texts = {}
    for r in train_rows:
        other_texts.setdefault(normalize(r["text"]), "train_candidate")
    for r in external:
        other_texts.setdefault(normalize(r.get("text")), "external_set")
    conflicts = [{"attempt_id": r["attempt_id"], "with": other_texts[normalize(r["text"])]}
                 for r in test_rows if normalize(r["text"]) in other_texts]
    if drop_conflicts and conflicts:
        drop = {c["attempt_id"] for c in conflicts}
        test_rows = [r for r in test_rows if r["attempt_id"] not in drop]
    external_users = {r.get("user") or r.get("player_pseudonym") for r in external} - {None}
    test_users = {r["player_pseudonym"] for r in test_rows}
    assigned_eval = set(eval_users) | test_users  # 문장이 없거나 검수로 빠진 평가 사용자도 검사한다
    user_overlap = sorted((assigned_eval & set(train_users)) | (assigned_eval & external_users))
    remaining_conflicts = [] if drop_conflicts else conflicts
    report = {
        "freeze_version": FREEZE_VERSION, "normalize_version": NORMALIZE_VERSION, "seed": seed,
        "odd_rule": "eval=floor(n/2)", "participants": len(ordered), "eval_users": len(eval_users),
        "train_users": len(train_users), "sentences_usertest": len(usertest), "sentences_pm": len(pm_rows),
        "test_human": len(test_rows), "test_human_pm": sum(1 for r in test_rows if r["pm"]),
        "test_human_without_pm": sum(1 for r in test_rows if not r["pm"]),
        "privacy_excluded": len(excluded), "privacy_excluded_train": len(train_all) - len(train_rows),
        "privacy_reasons": sorted({privacy_excluded[r["attempt_id"]] for r in excluded + train_all
                                   if r["attempt_id"] in privacy_excluded}),
        "sentence_conflicts": len(conflicts), "conflicts_dropped": len(conflicts) if drop_conflicts else 0,
        "user_overlap": len(user_overlap),
        "freeze_record": "manifest.json(sha256)",
    }
    report["ok"] = report["user_overlap"] == 0 and not remaining_conflicts and len(eval_users) > 0
    assignment = {"seed": seed, "freeze_version": FREEZE_VERSION, "participants_sorted": ordered,
                  "eval_users": eval_users, "train_users": train_users}
    return {"report": report, "assignment": assignment, "test_human": test_rows, "train_candidates": train_rows,
            "conflicts": remaining_conflicts or conflicts, "user_overlap": user_overlap}


def freeze_manifest(folder, names):
    """동결 폴더 파일의 바이트 수·sha256 목록. Delta 버전 대신 동결 기록으로 쓴다(11/8 사용자 결정)."""
    files = []
    for name in names:
        data = (Path(folder) / name).read_bytes()
        files.append({"name": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return {"freeze_version": FREEZE_VERSION, "files": files}


def _load_games(path):
    return json.loads(Path(path).read_text(encoding="utf-8")).get("games", []) if path else []


def _load_rows(path):
    text = Path(path).read_text(encoding="utf-8").strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def main(argv=None):
    """동결 폴더에 test_human.jsonl·train_candidates.jsonl·assignment.json·report.json을 쓴다. 검사 실패면 report_failed.json만."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", required=True, help="정식 테스트 판 내보내기")
    parser.add_argument("--pretest-games", help="사전 테스트 판 내보내기(PM 문장)")
    parser.add_argument("--pm-pseudonym", action="append", default=[])
    parser.add_argument("--session-id", action="append", default=[])
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--exclude-set", action="append", required=True,
                        help="학습·개발 세트(JSONL 또는 JSON 배열, text 필드). 필수, 비어 있으면 빈 파일을 명시")
    parser.add_argument("--privacy-review", required=True, help="필수, 제외가 없으면 {\"exclude\": []}. 형식 "
                        '{"exclude": [{"attempt_id": ..., "reason": ...}]}')
    parser.add_argument("--drop-conflicts", action="store_true")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    pm = set(args.pm_pseudonym)
    games = _load_games(args.games)
    usertest = sentences_of(games, pm, set(args.session_id))
    pretest = [r for r in sentences_of(_load_games(args.pretest_games), pm) if r["pm"]]
    external = [row for path in args.exclude_set for row in _load_rows(path)]
    review = json.loads(Path(args.privacy_review).read_text(encoding="utf-8"))
    excluded = {item["attempt_id"]: item.get("reason", "") for item in review.get("exclude", [])}
    result = build_freeze(usertest, pretest, args.seed, external, excluded, args.drop_conflicts,
                          players_of(games, pm, set(args.session_id)))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    detail = {**result["report"], "conflicts": result["conflicts"], "user_overlap_ids": result["user_overlap"]}
    print(json.dumps(result["report"], ensure_ascii=False, indent=2))
    if not result["report"]["ok"]:
        # 검사 실패: 동결 묶음은 쓰지 않는다(이전에 쓴 정상 묶음도 덮어쓰지 않음). 실패 보고만 남긴다.
        (out / "report_failed.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        sys.exit(1)
    _write_jsonl(out / "test_human.jsonl", result["test_human"])
    _write_jsonl(out / "train_candidates.jsonl", result["train_candidates"])
    (out / "assignment.json").write_text(json.dumps(result["assignment"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "report.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    names = ("test_human.jsonl", "train_candidates.jsonl", "assignment.json", "report.json")
    (out / "manifest.json").write_text(json.dumps(freeze_manifest(out, names), ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")


if __name__ == "__main__":
    main()
