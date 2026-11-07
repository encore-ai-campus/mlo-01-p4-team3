"""실제 sink의 저장된 ID/hash를 기준으로 공식 게임 이벤트의 중복을 제거한다."""
from pipeline.spark.bronze_contract import normalize,unique_rows


def plan_game_events(envelopes,existing_hashes):
    """sink 커밋 전에 새 행만 계획한다. existing_hashes는 durable sink에서 읽는다."""
    import json
    from engine.domain.serde import to_jsonable
    rows=[normalize(json.dumps(to_jsonable(e),ensure_ascii=False),topic=e.topic,key=e.game_id,partition=0,offset=i)
          for i,e in enumerate(envelopes)]
    return unique_rows(rows,existing_hashes)
