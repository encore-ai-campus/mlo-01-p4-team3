"""판 밖 진척을 source_event_id 기준으로 원자·멱등 반영한다."""
from __future__ import annotations
from engine.domain.model import PlayerProgress
from engine.domain.serde import from_jsonable, to_jsonable
from server.infra.mongo_repo import majority


class MongoProgressRepository:
    """각 플레이어 문서의 단서·결말·판 ID를 atomic update로 갱신한다."""

    def __init__(self, collection):
        self.collection=majority(collection)

    def _ensure(self, player):
        self.collection.update_one({'_id':player},{'$setOnInsert':{'progress':to_jsonable(PlayerProgress(player)),'applied':[]}},upsert=True)

    def load(self, player_pseudonym):
        """없는 플레이어는 기본 진척으로 반환하며 원본 객체 공유를 피한다."""
        document=self.collection.find_one({'_id':player_pseudonym})
        return from_jsonable(PlayerProgress,document['progress']) if document else PlayerProgress(player_pseudonym)

    def add_clue(self, player_pseudonym, clue_id, game_id, day, source_event_id):
        """동일 source 이벤트는 단서를 두 번 적재하지 않는다."""
        self._ensure(player_pseudonym)
        from pymongo import ReturnDocument
        clue={'clue_id':clue_id,'first_game_id':game_id,'first_day':day}
        lookup={'$getField':{'field':{'$literal':clue_id},'input':'$progress.clues'}}
        updated={'$cond':[{'$eq':[{'$type':lookup},'missing']},
                         {'$setField':{'field':{'$literal':clue_id},'input':'$progress.clues','value':{'$literal':clue}}},
                         '$progress.clues']}
        previous=self.collection.find_one_and_update({'_id':player_pseudonym,'applied':{'$ne':source_event_id}},
            [{'$set':{'progress.clues':updated,'applied':{'$concatArrays':['$applied',{'$literal':[source_event_id]}]}}}],
            return_document=ReturnDocument.BEFORE)
        return previous is not None and clue_id not in previous['progress']['clues']

    def unlock_ending(self, player_pseudonym, ending, source_event_id):
        """이벤트별로 한 번만 결말을 해제한다."""
        self._ensure(player_pseudonym)
        from pymongo import ReturnDocument
        previous=self.collection.find_one_and_update({'_id':player_pseudonym,'applied':{'$ne':source_event_id}},
            {'$addToSet':{'progress.endings_unlocked':ending,'applied':source_event_id}},return_document=ReturnDocument.BEFORE)
        return previous is not None and ending not in previous['progress']['endings_unlocked']

    def add_game(self, player_pseudonym, game_id):
        """동시 판 추가가 서로를 덮어쓰지 않게 집합으로 갱신한다."""
        self._ensure(player_pseudonym)
        self.collection.update_one({'_id':player_pseudonym},{'$addToSet':{'progress.game_ids':game_id}})
