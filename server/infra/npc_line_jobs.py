"""인프라: NPC 대사 작업 저장소 `<접두어>npc_line_jobs`(E-11-13, `npc_llm_design.md` 2·5절). 줄 하나 = 문서 하나.

- 회의 발언 다듬기(`kind="polish"`): `_id="<game_id>:<speech_id>"`. 선조회는 문서를 읽기만 하고, 화면 고정(`fix`)은
  `final`이 비어 있을 때만 한 번 저장한다(첫 확정이 이김).
- 캐묻기 답 실시간 생성(`kind="live"`): `_id="<game_id>:live:<job_id>"`. 마감(`deadline_at`) 안에 끝난 결과만 저장한다.

`review_jobs`와 같은 방식이다: 짧은 원자 연산만 두고, 완료 저장은 임대 토큰·처리 중·마감·미고정 조건부라 늦은 결과나
임대를 잃은 결과는 저장되지 않는다. 모든 갱신은 `upsert=False`라 판 삭제 뒤 문서를 다시 만들지 않는다.
대기 작업은 `priority`(0 = 실시간, 1 = 다듬기) 다음 등록 순서로 꺼낸다. 본문은 이 문서에만 둔다(이벤트·로그에 없음).
"""
from __future__ import annotations

import copy
import threading
from typing import Optional

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

COLLECTION = "npc_line_jobs"
_OPEN = ("queued", "running")


def _claimable(now: float) -> dict:
    return {"$or": [{"status": "queued"}, {"status": "running", "lease_until": {"$lt": now}}],
            "deadline_at": {"$gte": now}}


class MongoNpcLineStore:
    """`NpcLineStore` 포트의 Mongo 구현."""

    def __init__(self, collection):
        self.c = collection
        self.c.create_index([("status", ASCENDING), ("priority", ASCENDING), ("created_at", ASCENDING)],
                            name="status_priority_created")
        self.c.create_index([("game_id", ASCENDING), ("day", ASCENDING)], name="game_day")

    def insert_if_absent(self, doc: dict) -> bool:
        """$setOnInsert upsert로 멱등 등록한다."""
        try:
            res = self.c.update_one({"_id": doc["_id"]}, {"$setOnInsert": {k: v for k, v in doc.items() if k != "_id"}},
                                    upsert=True)
        except DuplicateKeyError:
            return False
        return res.upserted_id is not None

    def get(self, job_id: str) -> Optional[dict]:
        """작업 문서를 읽는다."""
        return self.c.find_one({"_id": job_id})

    def list_day(self, game_id: str, day: int, kind: str) -> list[dict]:
        """한 판·한 날의 작업 문서(등록 순)."""
        return list(self.c.find({"game_id": game_id, "day": day, "kind": kind}).sort("created_at", ASCENDING))

    def count_queued(self, kind: str, now: float) -> int:
        """종류별 대기 작업 수. 마감이 지난 대기 작업은 잡히지 않으므로 세지 않는다."""
        return self.c.count_documents({"status": "queued", "kind": kind, "deadline_at": {"$gte": now}})

    def claim_next(self, now: float, lease_until: float, token: str) -> Optional[dict]:
        """우선순위·등록 순으로 잡을 수 있는 작업 하나를 원자적으로 잡는다. 마감이 지난 작업은 잡지 않는다."""
        return self.c.find_one_and_update(
            _claimable(now), {"$set": {"status": "running", "claim_token": token, "lease_until": lease_until,
                                       "started_at": now}, "$inc": {"attempts": 1}},
            sort=[("priority", ASCENDING), ("created_at", ASCENDING)], return_document=ReturnDocument.AFTER)

    def finish(self, job_id: str, token: str, now: float, fields: dict) -> bool:
        """토큰·실행 중·마감 전·미고정 조건부로 완료한다."""
        res = self.c.update_one({"_id": job_id, "claim_token": token, "status": "running",
                                 "deadline_at": {"$gte": now}, "final": None},
                                {"$set": {**fields, "status": "done", "finished_at": now}}, upsert=False)
        return res.modified_count == 1

    def close(self, job_id: str, status: str, fields: dict, token: Optional[str] = None) -> bool:
        """열린 작업만 닫는다. 작업 프로세스는 자기 토큰을 넘겨, 임대가 끝나 다른 쪽이 다시 잡은 작업은 닫지 않는다."""
        query = {"_id": job_id, "status": {"$in": list(_OPEN)}}
        if token is not None:
            query["claim_token"] = token
        res = self.c.update_one(query,
                                {"$set": {**fields, "status": status}}, upsert=False)
        return res.modified_count == 1

    def fix(self, job_id: str, final: dict) -> Optional[dict]:
        """`final`이 비어 있을 때만 저장하고, 저장 뒤(또는 이미 고정된) 문서를 돌려준다. 문서가 없으면 None."""
        doc = self.c.find_one_and_update({"_id": job_id, "final": None},
                                         {"$set": {"final": final}},
                                         return_document=ReturnDocument.AFTER, upsert=False)
        return doc if doc is not None else self.c.find_one({"_id": job_id})

    def fix_stale(self, game_id: str, day: int, phase: str, finals: dict[str, dict]) -> int:
        """현재 날·단계가 아닌 미고정 작업을 주어진 값(작업 ID → final)으로 고정하고 열린 작업을 닫는다."""
        n = 0
        for job_id, final in finals.items():
            res = self.c.update_one({"_id": job_id, "game_id": game_id, "final": None,
                                     "$or": [{"day": {"$ne": day}}, {"phase": {"$ne": phase}}]},
                                    {"$set": {"final": final}}, upsert=False)
            n += res.modified_count
            self.c.update_one({"_id": job_id, "status": {"$in": list(_OPEN)}}, {"$set": {"status": "closed"}},
                              upsert=False)
        return n

    def delete(self, game_ids: list[str]) -> int:
        """판 삭제와 함께 작업 문서를 지운다."""
        return self.c.delete_many({"game_id": {"$in": list(game_ids)}}).deleted_count


class MemoryNpcLineStore:
    """같은 의미의 메모리 구현(시험·측정)."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def insert_if_absent(self, doc: dict) -> bool:
        with self._lock:
            if doc["_id"] in self.docs:
                return False
            self.docs[doc["_id"]] = copy.deepcopy(doc)
            return True

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            doc = self.docs.get(job_id)
            return copy.deepcopy(doc) if doc else None

    def list_day(self, game_id: str, day: int, kind: str) -> list[dict]:
        with self._lock:
            out = [d for d in self.docs.values() if d["game_id"] == game_id and d["day"] == day and d["kind"] == kind]
            return copy.deepcopy(sorted(out, key=lambda d: d["created_at"]))

    def count_queued(self, kind: str, now: float) -> int:
        with self._lock:
            return sum(1 for d in self.docs.values()
                       if d["status"] == "queued" and d["kind"] == kind and d["deadline_at"] >= now)

    def claim_next(self, now: float, lease_until: float, token: str) -> Optional[dict]:
        with self._lock:
            ready = [d for d in self.docs.values() if d["deadline_at"] >= now and
                     (d["status"] == "queued" or (d["status"] == "running" and d["lease_until"] < now))]
            if not ready:
                return None
            doc = min(ready, key=lambda d: (d["priority"], d["created_at"]))
            doc.update(status="running", claim_token=token, lease_until=lease_until, started_at=now,
                       attempts=doc["attempts"] + 1)
            return copy.deepcopy(doc)

    def finish(self, job_id: str, token: str, now: float, fields: dict) -> bool:
        with self._lock:
            doc = self.docs.get(job_id)
            if (not doc or doc.get("claim_token") != token or doc["status"] != "running" or now > doc["deadline_at"]
                    or doc.get("final") is not None):
                return False
            doc.update(copy.deepcopy(fields), status="done", finished_at=now)
            return True

    def close(self, job_id: str, status: str, fields: dict, token: Optional[str] = None) -> bool:
        with self._lock:
            doc = self.docs.get(job_id)
            if not doc or doc["status"] not in _OPEN or (token is not None and doc.get("claim_token") != token):
                return False
            doc.update(copy.deepcopy(fields), status=status)
            return True

    def fix(self, job_id: str, final: dict) -> Optional[dict]:
        with self._lock:
            doc = self.docs.get(job_id)
            if doc is None:
                return None
            if doc.get("final") is None:
                doc["final"] = copy.deepcopy(final)
            return copy.deepcopy(doc)

    def fix_stale(self, game_id: str, day: int, phase: str, finals: dict[str, dict]) -> int:
        n = 0
        with self._lock:
            for job_id, final in finals.items():
                doc = self.docs.get(job_id)
                if not doc or doc["game_id"] != game_id:
                    continue
                if doc.get("final") is None and (doc["day"], doc["phase"]) != (day, phase):
                    doc["final"] = copy.deepcopy(final)
                    n += 1
                if doc["status"] in _OPEN:
                    doc["status"] = "closed"
        return n

    def delete(self, game_ids: list[str]) -> int:
        with self._lock:
            gone = [k for k, d in self.docs.items() if d["game_id"] in game_ids]
            for k in gone:
                del self.docs[k]
            return len(gone)
