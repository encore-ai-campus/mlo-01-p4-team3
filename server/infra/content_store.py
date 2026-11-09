"""불변 콘텐츠 버전을 Mongo에 저장하며 기존 버전/단서 삭제를 허용하지 않는다.

E-09-09: 읽은 버전은 프로세스 안에 캐시한다. 같은 버전 덮어쓰기는 `insert`가 거부하므로
(`content_version_exists`) 버전 이름 하나로 내용이 정해지고, 캐시 무효화가 필요 없다.
"""
import threading

from engine.domain.content import ContentBundle
from engine.domain.serde import from_jsonable, to_jsonable
from server.infra.mongo_repo import majority


class MongoContentSource:
    """ContentSource 구현. 기존 버전은 변경하지 않고 새 버전만 추가한다."""

    def __init__(self, collection):
        self.collection=majority(collection)
        self._cache={}
        self._lock=threading.Lock()

    def load(self, content_version):
        """판 시작에 고정한 정확한 버전만 읽는다. 암묵적인 latest 교체는 없다.

        처음 읽은 버전은 역직렬화한 번들을 캐시해 같은 객체를 돌려준다(E-09-09). 없는 버전과
        조회 실패는 캐시하지 않아 다음 요청에서 다시 읽는다. 엔진은 번들을 읽기만 하고
        (`ContentPolicyEngine`도 버전마다 같은 번들로 정책을 만들어 판끼리 공유한다), 판 상태는
        번들 밖 `GameState`·`TruthState`에 있다.
        """
        bundle=self._cache.get(content_version)
        if bundle is not None: return bundle
        with self._lock:
            bundle=self._cache.get(content_version)
            if bundle is None:
                document=self.collection.find_one({'_id':content_version})
                if document is None: raise KeyError(content_version)
                bundle=self._cache[content_version]=from_jsonable(ContentBundle,document['bundle'])
            return bundle

    def insert(self, bundle):
        """전체 bundle을 엄격 변환한 뒤 새 버전으로만 삽입한다."""
        from pymongo.errors import DuplicateKeyError
        value=to_jsonable(bundle)
        validated=from_jsonable(ContentBundle,value)
        if not validated.content_version: raise ValueError('content_version_required')
        if validated.content_version=='__clue_catalog__': raise ValueError('reserved_content_version')
        clues={clue.clue_id for clue in validated.grudge_clues}
        from pymongo.read_concern import ReadConcern
        from pymongo.write_concern import WriteConcern
        def insert_version(session):
            catalog=self.collection.find_one({'_id':'__clue_catalog__'},session=session)
            previous=set(catalog['clue_ids']) if catalog else set()
            if not previous<=clues: raise ValueError('clue_id_removal_forbidden')
            self.collection.insert_one({'_id':validated.content_version,'bundle':value},session=session)
            self.collection.update_one({'_id':'__clue_catalog__'},
                {'$set':{'clue_ids':sorted(previous|clues)},'$setOnInsert':{'retired':[]}},upsert=True,session=session)
        try:
            with self.collection.database.client.start_session() as session:
                session.with_transaction(insert_version,read_concern=ReadConcern('snapshot'),write_concern=WriteConcern('majority'))
        except DuplicateKeyError:
            raise ValueError('content_version_exists') from None

    def retire_clues(self, clue_ids):
        """단서 원본을 지우지 않고 카탈로그에 폐기 표시만 추가한다."""
        catalog=self.collection.find_one({'_id':'__clue_catalog__'})
        if not catalog or not set(clue_ids)<=set(catalog['clue_ids']):raise ValueError('unknown_clue_id')
        self.collection.update_one({'_id':'__clue_catalog__'},{'$addToSet':{'retired':{'$each':list(clue_ids)}}})
