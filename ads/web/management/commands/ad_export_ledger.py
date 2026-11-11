"""광고 원장·일 예산을 하루 집계 작업(`ops/ads_aggregate.sh` ①)용 JSON으로 내보낸다."""
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ads.app.ledger_export import export_snapshot
from ads.infra.mysql_ledger import campaign_daily_budgets, export_ledger_rows


def _write(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


class Command(BaseCommand):
    """`--out` 폴더에 ledger.json·budgets.json을 쓰고 요약 한 줄(JSON)을 출력한다."""

    def add_arguments(self, parser):
        """출력 폴더 하나만 받는다."""
        parser.add_argument("--out", type=Path, required=True)

    def handle(self, *args, **options):
        """원장 전체를 읽어 원자적으로 저장한다. 실패하면 파일을 남기지 않는다."""
        out = options["out"]
        try:
            ledger, budgets, summary = export_snapshot(export_ledger_rows, campaign_daily_budgets)
            out.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write(out / "ledger.json", ledger)
            _write(out / "budgets.json", budgets)
        except (OSError, ValueError) as exc:
            raise CommandError(f"ledger export failed: {type(exc).__name__}") from None
        self.stdout.write(json.dumps(summary))
