"""인프라: 복기 해설 작업 저장소 `<접두어>review_jobs`(E-10-01, 설계 5.2). 판당 문서 1개, `_id=game_id`.

짧은 원자 연산만 둔다. LLM 호출은 저장소 밖(작업 프로세스)에서 하고, 완료 저장은 임대 토큰·처리 중·마감 조건부라
늦게 온 결과나 임대를 잃은 결과는 저장되지 않는다. 완료 저장은 `upsert=False`라 판 삭제 뒤 문서를 다시 만들지 않는다.
`MemoryReviewJobStore`는 시험·측정용 같은 의미의 메모리 구현이다.
"""
from __future__ import annotations

import copy
import threading
from typing import Optional

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

COLLECTION = "review_jobs"
_OPEN = ("queued", "running")


def _claimable(now: float) -> dict:
    return {"$or": [{"status": "queued"}, {"status": "running", "lease_until": {"$lt": now}}]}


class MongoReviewJobStore:
    """`ReviewJobStore` 포트의 Mongo 구현."""

    def __init__(self, collection):
        self.c = collection
        self.c.create_index([("status", ASCENDING), ("created_at", ASCENDING)], name="status_created")

    def insert_if_absent(self, doc: dict) -> bool:
        """$setOnInsert upsert로 멱등 등록한다."""
        try:
            res = self.c.update_one({"_id": doc["_id"]}, {"$setOnInsert": {k: v for k, v in doc.items() if k != "_id"}},
                                    upsert=True)
        except DuplicateKeyError:
            return False
        return res.upserted_id is not None

    def get(self, game_id: str) -> Optional[dict]:
        """판 ID로 작업 문서를 읽는다."""
        return self.c.find_one({"_id": game_id})

    def count_queued(self) -> int:
        """대기 중 작업 수."""
        return self.c.count_documents({"status": "queued"})

    def claim_next(self, now: float, lease_until: float, token: str) -> Optional[dict]:
        """가장 오래된 잡을 수 있는 작업을 원자적으로 잡고 시도 횟수를 올린다."""
        return self.c.find_one_and_update(
            _claimable(now), {"$set": {"status": "running", "claim_token": token, "lease_until": lease_until},
                              "$inc": {"attempts": 1}},
            sort=[("created_at", ASCENDING)], return_document=ReturnDocument.AFTER)

    def finish(self, game_id: str, token: str, now: float, fields: dict) -> bool:
        """토큰·실행 중·마감 전 조건부로 완료한다."""
        res = self.c.update_one({"_id": game_id, "claim_token": token, "status": "running", "deadline_at": {"$gte": now}},
                                {"$set": {**fields, "status": "done"}}, upsert=False)
        return res.modified_count == 1

    def close(self, game_id: str, status: str, fields: dict) -> bool:
        """열린 작업만 닫는다."""
        res = self.c.update_one({"_id": game_id, "status": {"$in": list(_OPEN)}},
                                {"$set": {**fields, "status": status}}, upsert=False)
        return res.modified_count == 1

    def delete(self, game_ids: list[str]) -> int:
        """판 삭제와 함께 작업 문서를 지운다."""
        return self.c.delete_many({"_id": {"$in": list(game_ids)}}).deleted_count


class MemoryReviewJobStore:
    """같은 의미의 메모리 구현(시험·측정)."""

    def __init__(self):
        self.docs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def insert_if_absent(self, doc: dict) -> bool:
        """같은 판 작업이 없을 때만 넣는다."""
        with self._lock:
            if doc["_id"] in self.docs:
                return False
            self.docs[doc["_id"]] = copy.deepcopy(doc)
            return True

    def get(self, game_id: str) -> Optional[dict]:
        """작업 문서 사본을 돌려준다."""
        with self._lock:
            doc = self.docs.get(game_id)
            return copy.deepcopy(doc) if doc else None

    def count_queued(self) -> int:
        """대기 중 작업 수."""
        with self._lock:
            return sum(1 for d in self.docs.values() if d["status"] == "queued")

    def claim_next(self, now: float, lease_until: float, token: str) -> Optional[dict]:
        """가장 오래된 잡을 수 있는 작업을 잡는다."""
        with self._lock:
            ready = [d for d in self.docs.values()
                     if d["status"] == "queued" or (d["status"] == "running" and d["lease_until"] < now)]
            if not ready:
                return None
            doc = min(ready, key=lambda d: d["created_at"])
            doc.update(status="running", claim_token=token, lease_until=lease_until, attempts=doc["attempts"] + 1)
            return copy.deepcopy(doc)

    def finish(self, game_id: str, token: str, now: float, fields: dict) -> bool:
        """토큰·실행 중·마감 전 조건부로 완료한다."""
        with self._lock:
            doc = self.docs.get(game_id)
            if not doc or doc.get("claim_token") != token or doc["status"] != "running" or now > doc["deadline_at"]:
                return False
            doc.update(copy.deepcopy(fields), status="done")
            return True

    def close(self, game_id: str, status: str, fields: dict) -> bool:
        """열린 작업만 닫는다."""
        with self._lock:
            doc = self.docs.get(game_id)
            if not doc or doc["status"] not in _OPEN:
                return False
            doc.update(copy.deepcopy(fields), status=status)
            return True

    def delete(self, game_ids: list[str]) -> int:
        """지운 작업 수를 돌려준다."""
        with self._lock:
            return sum(self.docs.pop(g, None) is not None for g in game_ids)
