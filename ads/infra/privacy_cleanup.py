"""광고 세션의 원장과 요청을 좁혀 MySQL에서 원자 삭제한다."""
from django.db import transaction
from django.db.models import Q, Sum

from ads.domain.model import BillingType, ChargeState
from ads.domain.rules import REFUND_SUFFIX, charge_id
from ads.infra.mysql_store import _digest
from ads.web.models import AdDailyBudget, AdLedgerEntry, AdOutbox, AdReceipt, AdSession, AdStoredRequest


def collect_scope(sessions):
    """세션 가명에서 요청·비콘·과금 및 환입 키를 찾는다."""
    requests = AdStoredRequest.objects.filter(session_id__in=sessions)
    request_ids = sorted(requests.values_list("pk", flat=True))
    serve_ids = sorted(requests.exclude(serve_id=None).values_list("serve_id", flat=True))
    charges = {charge_id(serve, billing) for serve in serve_ids for billing in BillingType}
    charge_keys = sorted(charges | {key + REFUND_SUFFIX for key in charges})
    outbox = AdOutbox.objects.filter(
        Q(payload__session_pseudonym__in=sessions) | Q(payload__serve_id__in=serve_ids)
        | Q(payload__request_id__in=request_ids) | Q(message_key__in=charge_keys)
    )
    events = sorted(outbox.values_list("event_id", flat=True))
    digests = {event.split(":")[1] for event in events if event.startswith("judged:")}
    receipts = AdReceipt.objects.filter(
        Q(pk__in=digests) | Q(pending_input__claims__session_pseudonym__in=sessions)
        | Q(result__charge_id__in=charge_keys)
    )
    return {"ad_sessions": sorted(sessions), "ad_serve_ids": serve_ids, "ad_request_ids": request_ids,
            "ad_event_ids": events, "ad_charge_ids": charge_keys,
            "receipt_ids": sorted(receipts.values_list("pk", flat=True))}


def _balance(budget):
    return AdLedgerEntry.objects.filter(budget=budget, state__in=(ChargeState.RESERVED, ChargeState.CONFIRMED)
                                       ).aggregate(total=Sum("amount_mw"))["total"] or 0


@transaction.atomic
def purge_scope(approved):
    """승인 범위만 지우고 변경된 일 예산을 남은 원장과 대사한다."""
    current = collect_scope(approved["ad_sessions"])
    if any(not set(values).issubset(approved[key]) for key, values in current.items()):
        raise ValueError("privacy_ad_scope_changed")
    ledger = AdLedgerEntry.objects.filter(pk__in=current["ad_charge_ids"])
    budget_ids = set(ledger.values_list("budget_id", flat=True))
    budgets = list(AdDailyBudget.objects.select_for_update().filter(pk__in=budget_ids).order_by("pk"))
    if any(budget.committed_mw != _balance(budget) for budget in budgets):
        raise ValueError("privacy_ad_ledger_already_inconsistent")
    counts = {
        "ledger": ledger.delete()[0],
        "outbox": AdOutbox.objects.filter(event_id__in=current["ad_event_ids"]).delete()[0],
        "receipts": AdReceipt.objects.filter(pk__in=current["receipt_ids"]).delete()[0],
        "requests": AdStoredRequest.objects.filter(pk__in=current["ad_request_ids"]).delete()[0],
        "sessions": AdSession.objects.filter(
            pk__in=[_digest(value, synthetic) for value in current["ad_sessions"] for synthetic in (True, False)]
        ).delete()[0],
    }
    for budget in budgets:
        budget.committed_mw = _balance(budget)
        if budget.committed_mw < 0:
            raise ValueError("privacy_ad_negative_balance")
        budget.save(update_fields=["committed_mw"])
    if (ledger.exists() or AdReceipt.objects.filter(pk__in=current["receipt_ids"]).exists()
            or any(collect_scope(current["ad_sessions"])[key] for key in current if key != "ad_sessions")):
        raise ValueError("privacy_ad_data_remaining")
    return counts | {"budgets_reconciled": len(budgets)}
