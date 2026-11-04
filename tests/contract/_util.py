"""계약 테스트 공용 도우미."""
from __future__ import annotations

import json
from pathlib import Path

from engine.domain import serde

ROOT = Path(__file__).resolve().parents[2]
FX = ROOT / "fixtures"


def load_json(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def load_jsonl(rel: str) -> list:
    return [json.loads(line) for line in (ROOT / rel).read_text(encoding="utf-8").splitlines() if line.strip()]


def strict(tp, data):
    """엄격 검증 후 왕복 직렬화가 원본과 같은지까지 확인한다."""
    obj = serde.from_jsonable(tp, data)
    assert serde.to_jsonable(obj) == data, f"왕복 불일치: {tp}"
    return obj
