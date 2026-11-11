"""NPC 대사 LLM 작업 프로세스(E-11-13). `python server/manage.py npc_line_worker [--once]`.

계층: 인프라(프로세스 실행). 운영 게임 서비스 조립(`PALDO_GAME_SERVICE_FACTORY`)을 그대로 써서 같은 Mongo·같은
`npc_line_jobs`를 본다. 한 번에 한 줄(HTTP 1회), 실시간(캐묻기 답) > 다듬기(회의 발언) 순. 복기 작업 프로세스
(`review_worker`)와 따로 돌아 생성 서버 슬롯 2개를 하나씩 쓴다(복기가 두 슬롯을 다 차지하지 않는다).
`PALDO_NPC_LLM_URL`이 비어 있으면 기능 꺼짐이라 바로 끝난다. 모드가 off여도 프로세스는 기다리며, 꺼낸 작업은
템플릿으로 닫는다. 판 ID·문장 본문은 로그에 남기지 않는다.
"""
import time

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """manage.py npc_line_worker: NPC 대사 작업을 하나씩 처리하는 작업 프로세스(1개만 띄운다)."""
    help = "NPC 대사 LLM 작업(실시간 > 다듬기)을 하나씩 처리한다."

    def add_arguments(self, parser):
        """--once(대기 작업이 없을 때까지만), --idle(빈 큐 대기 초)."""
        parser.add_argument("--once", action="store_true", help="대기 작업이 없을 때까지 처리하고 끝낸다")
        parser.add_argument("--idle", type=float, default=0.2, help="대기 작업이 없을 때 쉬는 초")

    def handle(self, *args, **options):
        """주소가 없으면 CommandError로 끝내고, 있으면 작업을 계속 처리한다."""
        from server.paldo_web.composition import get_game_service
        from tools.jsonlog import log
        lines = getattr(get_game_service(), "npc_lines", None)
        if lines is None or lines.narrator is None:
            raise CommandError("npc_llm_disabled")
        while True:
            try:
                outcome = lines.process_next()
            except Exception as exc:  # 한 작업의 저장소 오류로 프로세스가 죽지 않게 한다(임대 만료 뒤 재시도)
                log("npc_line_worker", "error", "작업 처리 실패", error=type(exc).__name__)
                outcome = None
                time.sleep(1.0)
            if outcome is not None:
                log("npc_line_worker", "info", "작업 처리", kind=outcome.kind, status=outcome.status,
                    queue_ms=outcome.queue_ms, gen_ms=outcome.gen_ms, reason=outcome.reason, saved=outcome.saved)
                continue
            if options["once"]:
                return
            time.sleep(options["idle"])
