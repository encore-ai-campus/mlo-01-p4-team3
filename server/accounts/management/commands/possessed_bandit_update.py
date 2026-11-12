"""씐 자 전략 밴딧 하루 갱신(D-09-02, 11/12 운영 연결). 매일 01:10(서울) 타이머가 1회 부른다.

- 기본: 밴딧 기록이 있는 동의 사람 판의 종료·설문 이벤트로 새 버전을 게시한다(새 설문 응답 20판 미만이면 게시 없음).
  지워졌거나 동의가 빠진 판은 먼저 밴딧 상태에서 뺀다.
- `--init`: 초기 상태(`bandit-0`, 확정 팔 균등)만 만든다. 이미 있으면 그대로 두고, 팔 구성이 다르면 실패한다.

출력은 건수만 남긴다(가명·판 ID는 출력하지 않음). 판 웹 요청은 이 명령을 부르지 않는다.
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from server.accounts.models import AccountProfile


def consented_pseudonyms() -> set[str]:
    """연구 동의가 있고 활성인 일반 계정의 참가자 가명. 운영자·잠긴(삭제 요청) 계정은 뺀다."""
    rows = AccountProfile.objects.select_related("user").filter(
        consented_at__isnull=False, user__is_active=True, user__is_staff=False, user__is_superuser=False)
    return {str(p.player_pseudonym) for p in rows}


class Command(BaseCommand):
    """밴딧 상태를 초기화하거나 하루 1회 갱신한다."""
    help = "씐 자 전략 밴딧: --init 은 초기 상태만 만들고, 인자 없이는 하루 갱신을 1회 실행합니다."

    def add_arguments(self, parser):
        parser.add_argument("--init", action="store_true")

    def handle(self, *args, **options):
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError
        from pymongo.read_concern import ReadConcern
        from ml.bandit import latest_version, load_arms
        from ml.mongo_bandit import MongoBanditStore
        from server.infra.possessed_bandit import bandit_collection, run_update
        from tools.jsonlog import log

        if not settings.SESSION_MONGO_URI:
            raise CommandError("possessed_bandit_mongo_required")
        try:
            with MongoClient(settings.SESSION_MONGO_URI, timeoutMS=60000, serverSelectionTimeoutMS=5000) as client:
                database = client.get_default_database("paldo_web", read_concern=ReadConcern("majority"))
                store = MongoBanditStore(bandit_collection(database), load_arms())
                if options["init"]:
                    result = {"version": latest_version(store.load()), "initialized": True}
                else:
                    published, forgotten, games = run_update(database["games"], store, consented_pseudonyms())
                    result = {"published": published, "forgotten": forgotten, "games": games,
                              "version": latest_version(store.load())}
        except (PyMongoError, ValueError, KeyError) as error:
            log("possessed_bandit", "error", "밴딧 갱신 실패", error_type=type(error).__name__)
            raise CommandError(f"possessed_bandit_failed:{type(error).__name__}") from None
        log("possessed_bandit", "info", "밴딧 갱신", **result)  # 표준 출력에도 같은 한 줄이 나간다
