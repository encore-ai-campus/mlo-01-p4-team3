"""Scenario 6: replay held-out result-screen contexts and score against separate truth."""
import argparse
import json
import random
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path

from ads.app.beacon import handle_impression
from ads.app.click import handle_click
from ads.app.serve import ATTRIBUTION_TZ, AdService
from ads.domain.model import AdContext, AdRequest, AdSlot, BillingType, GameOutcome
from ads.domain.rules import RELEASE_AFTER_MS, verify_token
from tools.ad_scenarios import _world, run_policy
from tools.build_ads_fixtures import slot_policies
from tools.evidence_result import write_result

SEEDS = (17, 29, 43)
START = datetime(2026, 11, 1, tzinfo=ATTRIBUTION_TZ)
EVAL_FROM = "2026-11-04"
SLOT = AdSlot.RESULT_BANNER
CAMPAIGN = "syn:result-ctr"
CREATIVE = CAMPAIGN + ":creative"


def prepare(out, requests):
    """Produce auction/judgment events and ledger separately from evaluation probabilities."""
    if requests < 240:
        raise ValueError("requests per day must be >= 240")
    out.mkdir(parents=True, exist_ok=True)
    template = _world(1)[0][0]
    creative = replace(template.creatives[0], campaign_id=CAMPAIGN, creative_id=CREATIVE)
    ledger, budgets, truth = [], {}, []
    policies = {p.slot: p for p in slot_policies()}
    with (out / "bronze.jsonl").open("w", encoding="utf-8") as handle:
        def emit(topic, event):
            handle.write(json.dumps(dict(topic=topic, envelope_json=json.dumps(event))) + "\n")

        for offset, seed in enumerate((*[1000 + s for s in SEEDS], *SEEDS)):
            date = START + timedelta(days=offset)
            day = date.date().isoformat()
            start = int(date.timestamp() * 1000)
            now = start
            campaign = replace(template, campaign_id=CAMPAIGN, creatives=[creative], billing=BillingType.CPC,
                               slots=[SLOT], max_bid_mw=120_000, daily_budget_mw=10**10,
                               start_date=day, end_date=day)
            budgets[day] = {CAMPAIGN: campaign.daily_budget_mw}
            service = AdService(lambda: [campaign], policies, b"scenario-six", clock=lambda: now)
            rng = random.Random(seed)
            counts = defaultdict(lambda: [0, 0.])
            for i in range(requests):
                now = start + i * 86_400_000 // requests
                hour = (now - start) // 3_600_000
                outcome = GameOutcome.WIN if i % 2 else GameOutcome.LOSE
                preset = ("base6", "large8")[i // 2 % 2]
                p = (.22 if outcome is GameOutcome.WIN else .065) * (1.08 if preset == "large8" else 1.)
                p *= .94 if hour < 12 else 1.06
                rid = f"syn:context:{seed}:{i}"
                req = AdRequest(rid, SLOT, rid, AdContext(preset, outcome, hour=hour), "synthetic")
                response = service.serve(req)
                claims, _ = verify_token(response.serve_token, service.key, now) if response.filled else (None, None)
                if not claims:
                    continue
                record = service.state.serves[claims.serve_id]
                base = dict(request_id=rid, serve_id=record.serve_id, slot=SLOT, campaign_id=CAMPAIGN,
                            creative_id=CREATIVE, source="synthetic", billing="cpc", price_mw=record.price_mw,
                            occurred_at_ms=now, received_at_ms=now, load_test=False)
                emit("ad.serve", base | dict(event_id=rid+":serve", filled=True, context=asdict(req.context)))
                impression = handle_impression(service, response.serve_token, 1., 1200)
                if impression.judgement.status.value != "valid":
                    raise AssertionError("scenario impression was invalid")
                emit("ad.impression", base | dict(event_id=rid+":impression", status="valid",
                     visible_ratio=1., visible_ms=1200, charge_mw=impression.charge_mw, charge_id=impression.charge_id))
                if rng.random() < p:
                    click = handle_click(service, response.serve_token)
                    if click.judgement.status.value != "valid":
                        raise AssertionError("scenario click was invalid")
                    emit("ad.click", base | dict(event_id=rid+":click", status="valid",
                         charge_mw=click.charge_mw, charge_id=click.charge_id))
                if day >= EVAL_FROM:
                    row = counts[day, SLOT.value, CREATIVE, outcome.value, preset, hour]
                    row[0] += 1
                    row[1] += p
            ledger.extend(asdict(row) | {"source": "synthetic"} for row in service.book.rows.values())
            truth.extend([seed, *key, *values] for key, values in sorted(counts.items()))
    as_of_ms = int((START + timedelta(days=6)).timestamp() * 1000) + RELEASE_AFTER_MS + 1
    for name, payload in (("ledger", ledger), ("budgets", budgets), ("truth", truth), ("cutoff", as_of_ms)):
        (out / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    print(json.dumps(dict(eval_from=EVAL_FROM, cutoff_ms=as_of_ms, requests_per_day=requests, eval_seeds=SEEDS)))


def score(out, report_path):
    """Score each held-out seed/outcome after verifying the same impressions reached Delta."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    predictions = {tuple(row[:-3]): tuple(row[-3:]) for row in report["evaluation"]}
    totals = defaultdict(lambda: [0, 0., 0., 0.])
    for seed, day, slot, creative, outcome, preset, hour, n, expected in truth:
        observed, beta, model = predictions.pop((day, slot, creative, outcome, preset, hour))
        if observed != n:
            raise ValueError("held-out impressions differ between truth and gold")
        row = totals[seed, outcome]
        for i, value in enumerate((n, expected, n * beta, n * model)):
            row[i] += value
    if predictions or set(totals) != {(seed, outcome.value) for seed in SEEDS for outcome in GameOutcome}:
        raise ValueError("scenario coverage mismatch")
    rows = [dict(seed=seed, outcome=outcome, impressions=n, true_ctr=expected/n, beta=beta/n, lightgbm=model/n,
                 beta_error=abs(beta-expected)/expected, lightgbm_error=abs(model-expected)/expected)
            for (seed, outcome), (n, expected, beta, model) in sorted(totals.items())]
    maximum = max(row["lightgbm_error"] for row in rows)
    correct = sum(totals[seed, "win"][3] / totals[seed, "win"][0] >
                  totals[seed, "lose"][3] / totals[seed, "lose"][0] for seed in SEEDS)
    (out / "scenario6.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows))
    write_result("D-10-02", "scenario6", {
        "ctr_relative_error_max": dict(value=maximum, unit=""),
        "correct_outcome_order": dict(value=correct, unit="건"),
        "beta_log_loss": dict(value=report["beta"]["log_loss"], unit=""),
        "lightgbm_log_loss": dict(value=report["lightgbm"]["log_loss"], unit=""),
        "lightgbm_ece": dict(value=report["lightgbm"]["ece"], unit=""),
    }, "python -m tools.ad_ctr_scenario score --out " + str(out) + " --report " + str(report_path))
    return int(maximum > .2 or correct != len(SEEDS))


def pacing(out, requests):
    """Re-run scenario 1 with the old and current controller over six seeds."""
    seeds = (*SEEDS, 59, 71, 83)
    rows = []
    for seed in seeds:
        for label, options in (("before", {"pacing_gain": 5.}), ("after", {})):
            metrics = run_policy(1, seed, requests, "thompson", **options)["metrics"]
            row = dict(seed=seed, controller=label, **{key: metrics[key] for key in
                ("budget_error", "budget_curve_mae", "ctr_relative_error", "invalid_charges")})
            rows.append(row)
            print(json.dumps(row), flush=True)
    before = [row for row in rows if row["controller"] == "before"]
    after = [row for row in rows if row["controller"] == "after"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "pacing.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    error = max(row["budget_error"] for row in after)
    invalid = sum(row["invalid_charges"] for row in after)
    write_result("D-10-02", "pacing", dict(
        budget_error_max=dict(value=error, unit=""), invalid_charges=dict(value=invalid, unit="건"),
        curve_mae_before=dict(value=sum(row["budget_curve_mae"] for row in before) / len(before), unit=""),
        curve_mae_after=dict(value=sum(row["budget_curve_mae"] for row in after) / len(after), unit=""),
    ), f"python -m tools.ad_ctr_scenario pacing --out {out} --requests {requests}")
    return int(error > .1 or invalid > 0 or any(row["ctr_relative_error"] > .2 for row in after))


def main(argv=None):
    """Generate independently seeded days or score predictions after C silver/Delta gold."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "score", "pacing"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--requests", type=int, default=12_000)
    args = parser.parse_args(argv)
    if args.action == "prepare":
        prepare(args.out, args.requests)
        return 0
    if args.action == "pacing":
        return pacing(args.out, args.requests)
    if not args.report:
        parser.error("score requires --report")
    return score(args.out, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
