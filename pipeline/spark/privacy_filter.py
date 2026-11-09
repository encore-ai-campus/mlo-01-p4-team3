"""삭제 요청이 등록된 원본이 Bronze에 다시 들어오지 않도록 차단한다."""
import json
from pathlib import Path

from tools.privacy_delete import FIELDS


def load_tombstones(directory):
    """설정된 디렉터리의 요청을 합친다. 잘못된 파일은 적재를 중단한다."""
    path = Path(directory)
    if not path.is_dir():
        raise ValueError("privacy_tombstone_directory_unavailable")
    identities = {field: set() for field in FIELDS}
    for file in path.glob("request-*.json"):
        row = json.loads(file.read_text(encoding="utf-8"))
        for field in FIELDS:
            values = row[field]
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                raise ValueError("invalid_tombstone_values")
            identities[field].update(values)
    return identities


def retained_rows(rows, identities):
    """게임 가명, 광고 세션 가명 및 연관 재판정 키가 없는 원본만 남긴다."""
    for row in rows:
        event = json.loads(row["envelope_json"])
        if (event.get("player_pseudonym") in identities["players"]
                or event.get("session_pseudonym") in identities["ad_sessions"]
                or event.get("event_id") in identities["ad_event_ids"]
                or event.get("source_event_id") in identities["ad_event_ids"]
                or event.get("charge_id") in identities["ad_charge_ids"]
                or event.get("reserved_charge_id") in identities["ad_charge_ids"]):
            continue
        yield row
