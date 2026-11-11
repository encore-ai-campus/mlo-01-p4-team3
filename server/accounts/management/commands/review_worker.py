"""복기 해설 LLM 요약 작업 프로세스(E-10-01). `python server/manage.py review_worker [--once]`.

계층: 인프라(프로세스 실행). 운영 게임 서비스 조립(`PALDO_GAME_SERVICE_FACTORY`)을 그대로 써서 같은 Mongo·같은
`review_jobs`를 본다. 동시 처리 1건, FIFO. 할 일이 없으면 `--idle` 초 쉬고 다시 본다.
`PALDO_REVIEW_LLM_URL`이 비어 있으면 기능 꺼짐이라 바로 끝난다. 판 ID·문장 본문은 로그에 남기지 않는다.
"""
import time

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """manage.py review_worker: 복기 해설 작업을 FIFO로 하나씩 처리하는 작업 프로세스(1개만 띄운다)."""
    help = "복기 해설 LLM 요약 작업을 하나씩 처리한다."

    def add_arguments(self, parser):
        """--once(한 건만), --idle(빈 큐 대기 초)."""
        parser.add_argument("--once", action="store_true", help="대기 작업이 없을 때까지 처리하고 끝낸다")
        parser.add_argument("--idle", type=float, default=1.0, help="대기 작업이 없을 때 쉬는 초")

    def handle(self, *args, **options):
        """기능이 꺼져 있으면 CommandError로 끝내고, 켜져 있으면 작업을 계속 처리한다(--once면 큐가 빌 때 끝냄)."""
        from server.paldo_web.composition import get_game_service
        from tools.jsonlog import log
        narration = getattr(get_game_service(), "narration", None)
        if narration is None or not narration.enabled:
            raise CommandError("review_narration_disabled")
        while True:
            try:
                outcome = narration.process_next()
            except Exception as exc:  # 한 작업의 저장소 오류로 프로세스가 죽지 않게 한다(임대 만료 뒤 재시도)
                log("review_worker", "error", "작업 처리 실패", error=type(exc).__name__)
                outcome = None
                time.sleep(options["idle"])
            if outcome is not None:
                log("review_worker", "info", "작업 처리", status=outcome.status, queue_ms=outcome.queue_ms,
                    gen_ms=outcome.gen_ms, tokens_out=outcome.tokens_out, llm_npcs=outcome.llm_npcs,
                    template_npcs=outcome.template_npcs, rejects=outcome.rejects, saved=outcome.saved)
                continue
            if options["once"]:
                return
            time.sleep(options["idle"])
