"""NPC 대사 LLM 모드 바꾸기(E-11-13, `npc_llm_design.md` D5). 재배포 없이 운영 설정 문서를 고친다.

`python server/manage.py npc_llm_mode {off|polish|live|show} [--live-deadline-ms N] [--polish-queue-max N]`

계층: 표현(CLI 인자) → 인프라 `server/infra/ops_settings.py`. 웹 프로세스는 10초 안에, 작업 프로세스는 다음 작업부터
반영한다. `PALDO_NPC_LLM_URL`이 비어 있는 환경에서는 문서를 바꿔도 off다(상한 스위치).
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    """manage.py npc_llm_mode: off | polish | live로 바꾸거나 show로 현재 값을 본다."""
    help = "NPC 대사 LLM 모드(off|polish|live)를 운영 설정 문서에 저장하거나(show) 현재 값을 보여 준다."

    def add_arguments(self, parser):
        parser.add_argument("mode", choices=("off", "polish", "live", "show"))
        parser.add_argument("--live-deadline-ms", type=int, default=None, help="실시간 생성 마감(ms, 200~15000)")
        parser.add_argument("--polish-queue-max", type=int, default=None, help="다듬기 대기 상한(0~1000)")

    def handle(self, *args, **options):
        from server.paldo_web.composition import get_game_service
        lines = getattr(get_game_service(), "npc_lines", None)
        if lines is None:
            raise CommandError("npc_llm_not_assembled")
        settings = lines.settings
        if options["mode"] != "show":
            try:
                settings.set_mode(options["mode"], options["live_deadline_ms"], options["polish_queue_max"])
            except ValueError as exc:
                raise CommandError(str(exc)) from None
        cfg = settings.fresh()
        url_set = lines.narrator is not None
        self.stdout.write(f"mode={cfg.mode} live_deadline_ms={cfg.live_deadline_ms} "
                          f"polish_queue_max={cfg.polish_queue_max} url_set={url_set}")
