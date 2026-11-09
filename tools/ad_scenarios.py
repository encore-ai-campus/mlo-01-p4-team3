"""Independent local evaluation of ad scenarios 1..5; never a Kafka/Delta claim.

Truth stays in this evaluator. Only final observable N/C cells reach build_ctr.
An explicit oracle evaluation policy reads truth; production and learned
policies do not. Regret compares full-day runs with the same resource rules.
"""
import argparse
import hashlib
import heapq
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from ads.app.beacon import handle_impression
from ads.app.click import handle_click
from ads.app.ctr_update import build_ctr
from ads.app.pacing_update import build_pacing
from ads.app.serve import AdService
from ads.domain.model import (
    AdContext, AdRequest, AdSlot, BillingType, Campaign, CampaignStatus,
    ChargeState, Creative, GameOutcome, ServeClaims, SlotPolicy, Targeting,
)
from ads.domain.rules import (
    GRACE_MS, RELEASE_AFTER_MS, TOKEN_TTL_MS,
    sign_token,
)
from tools.evidence_result import write_result
from tools.jsonlog import log

ROOT = Path(__file__).resolve().parents[1]
BASE_MS = 1794063600000  # 2026-11-08 00:00 KST
DAY = "2026-11-08"
SLOT = AdSlot.LOBBY_BANNER
CONCLUSION = "알고리즘·회계·제어 루프 동작 검증"


def _uniform(seed, *parts):
    value = hashlib.sha256(json.dumps([seed, *parts], separators=(",", ":")).encode()).digest()
    return int.from_bytes(value[:8], "big") / 2**64


def _world(scenario):
    def campaign(name, billing, bid, budget, rates):
        cid = "syn:" + name
        creatives = [Creative(cid + f":cr{i}", cid, "evaluation", "evaluation", "/static/ads/eval.png",
                              "/ads/landing/" + cid + f":cr{i}") for i in range(len(rates))]
        c = Campaign(cid, "syn:advertiser", name, list(AdSlot), billing, bid, budget, Targeting(),
                     DAY, DAY, CampaignStatus.ACTIVE, creatives)
        return c, dict(zip((cr.creative_id for cr in creatives), rates))
    if scenario == 1:
        specs = [campaign("paced", BillingType.CPM, 500_000, 400_000, [.10, .14])]
    elif scenario == 2:
        specs = [campaign("quality", BillingType.CPC, 100_000, 10**12, [.16]),
                 campaign("bidder", BillingType.CPC, 200_000, 10**12, [.04])]
    elif scenario == 3:
        specs = [campaign("new", BillingType.CPM, 500_000, 10**12, [.06, .16])]
    elif scenario == 4:
        specs = [campaign("exhaust", BillingType.CPM, 300_000, 80_000, [.12]),
                 campaign("fallback", BillingType.CPM, 200_000, 10**12, [.10])]
    else:
        specs = [campaign("click", BillingType.CPC, 80_000, 10**12, [.12, .08]),
                 campaign("view", BillingType.CPM, 4_000_000, 10**12, [.07, .11])]
    return [s[0] for s in specs], {k: v for _, rates in specs for k, v in rates.items()}


def _probability(base, seed, index, requests, session_views, context):
    temporal = 1 + .10 * math.sin(2 * math.pi * index / requests)
    user = .75 if int(index // 4) % 2 == 0 else 1.25
    outcome = 1.05 if context.outcome is GameOutcome.WIN else .95
    return min(.95, base * temporal * user * outcome * .9**session_views)


def _final_snapshot(service, now, sequence):
    cells = defaultdict(lambda: [0, 0])
    for rec in service.state.serves.values():
        if rec.served_at_ms + RELEASE_AFTER_MS >= now or rec.serve_id not in service.state.impressed:
            continue
        hour = int((rec.served_at_ms - BASE_MS) // 3_600_000)
        cells[rec.campaign_id, rec.creative_id, rec.slot, hour][0] += 1
        cells[rec.campaign_id, rec.creative_id, rec.slot, hour][1] += int(rec.serve_id in service.state.clicked)
    return dict(finalized=True, source_version=f"local-final:{sequence}", as_of_ms=now, rows=[
        dict(campaign_id=cid, creative_id=cr, slot=slot.value, source="synthetic",
             attribution_day=DAY, hour=hour, valid_impressions=n, clicked_impressions=c)
        for (cid, cr, slot, hour), (n, c) in sorted(cells.items())])


class _BaselineService(AdService):
    def rank_ctr(self, campaign, creative, policy, rng):
        """Compare random CTR scores or fixed prior greedy under the same auction rules."""
        return rng.random() if self.evaluation_policy == "random" else policy.prior_ctr


class _OracleService(AdService):
    def rank_ctr(self, campaign, creative, policy, rng):
        """Evaluation-only true-CTR reference; never selected by production composition."""
        return self.evaluation_probabilities[creative.creative_id]


def run_policy(scenario, seed, requests, policy_name):
    """Run one simulated day through the actual service, judges and BudgetBook.

    random is independent Uniform(0,1) CTR ranking/creative selection; fixed
    uses the unchanging slot prior. All retain identical bids, budgets, cap,
    exploration floor and pacing controller. No training generator is used.
    """
    campaigns, rates = _world(scenario)
    now = [BASE_MS]
    policies = {slot: SlotPolicy(slot, 100_000, .08) for slot in AdSlot}
    cls = {"thompson": AdService, "random": _BaselineService,
           "fixed": _BaselineService, "oracle": _OracleService}[policy_name]
    service = cls(lambda: campaigns, policies, b"isolated-evaluation-key", clock=lambda: now[0])
    service.evaluation_policy = policy_name
    new_creative = campaigns[0].creatives.pop() if scenario == 3 else None
    queue, serial, click_tokens = [], 0, []
    metrics = dict(invalid_charges=0, delayed_clicks=0, lost_beacons=0, fatigued_views=0,
                   temporal_changes=requests, heterogeneous_users=requests,
                   posterior_replay_mismatches=0, gold_reconciliation_mismatches=None)
    truth, events, predictions = [], [], []
    views, true_sum, observed, prediction_sum = Counter(), Counter(), Counter(), Counter()
    winner_counts, invalid_types = Counter(), Counter({k: 0 for k in ("duplicate", "bad_signature", "no_serve", "expired")})
    before, after = [], []
    last_bucket = None

    def click(token):
        result = handle_click(service, token)
        metrics["invalid_charges"] += int(result.judgement.status.value != "valid" and result.charge_mw != 0)
        if result.judgement.status.value == "valid":
            click_tokens.append(token)
            if scenario == 5 and len(click_tokens) % 4 == 0:
                attack = handle_click(service, token)
                invalid_types["duplicate"] += int(attack.judgement.reason.value == "duplicate")
                metrics["invalid_charges"] += int(attack.charge_mw != 0)
        return result

    def flush(until):
        while queue and queue[0][0] <= until:
            instant, _, token = heapq.heappop(queue)
            now[0] = instant
            click(token)
        now[0] = until

    for i in range(requests):
        instant = BASE_MS + int(i * 86_400_000 / requests)
        flush(instant)
        if new_creative and i == requests // 2:
            campaigns[0].creatives.append(new_creative)
        bucket = int((instant - BASE_MS) // 300_000)
        if bucket != last_bucket:
            snapshot = _final_snapshot(service, instant, bucket)
            rows = build_ctr(snapshot, policies)
            metrics["posterior_replay_mismatches"] += int(rows != build_ctr(snapshot, policies))
            if policy_name == "thompson":
                service.ctr = {(r["creative_id"], AdSlot(r["slot"])): (r["alpha"], r["beta"]) for r in rows}
            service.ctr_version = "local-beta:" + str(bucket) if policy_name == "thompson" else policy_name
            previous = {cid: p for (cid, day), p in service.pacing.items() if day == DAY}
            pacing = build_pacing(campaigns, service.book, DAY, bucket * 5, previous)
            service.pacing = {(r["campaign_id"], DAY): r["probability"] for r in pacing}
            service.policy_version = "local-pacing:" + str(bucket)
            last_bucket = bucket
        session = f"syn:s{seed}:{i // 4}"
        slot = list(AdSlot)[i % len(AdSlot)]
        ctx = AdContext("base6", GameOutcome.WIN if i % 2 else GameOutcome.LOSE, i % 3 == 0,
                        int((instant - BASE_MS) // 3_600_000))
        probs = {cr.creative_id: _probability(rates[cr.creative_id] * (.9 + .1 * (i % 3)), seed, i, requests,
                    views[session, cr.creative_id], ctx) for c in campaigns for cr in c.creatives}
        if policy_name == "oracle":
            service.evaluation_probabilities = probs
        req = AdRequest(f"syn:e{scenario}:s{seed}:r{i}", slot, session, ctx, "synthetic")
        response = service.serve(req)
        events.append(dict(request_id=req.request_id, filled=response.filled,
                           ctr_version=response.ctr_version, policy_version=response.policy_version))
        if not response.filled:
            continue
        rec = next(reversed(service.state.serves.values()))
        p = probs[rec.creative_id]
        winner_counts[rec.campaign_id] += int(i >= requests // 3)
        if scenario == 4:
            if rec.campaign_id == "syn:exhaust":
                before.append(rec.price_mw)
            elif any(c.campaign_id == "syn:exhaust" and c.filtered == "budget"
                     for c in service.candidates[req.request_id]):
                after.append(rec.price_mw)
        seen = _uniform(seed, i, "viewable") >= .03
        lost = _uniform(seed, i, "beacon") < .02
        clicked = seen and _uniform(seed, i, rec.creative_id, "click") < p
        if seen:
            metrics["fatigued_views"] += int(views[session, rec.creative_id] > 0)
            views[session, rec.creative_id] += 1
        if seen and not lost:
            handle_impression(service, response.serve_token, 1., 1200)
        metrics["lost_beacons"] += int(seen and lost)
        delay = 1000 + int(_uniform(seed, i, "delay") * 89_000)
        if clicked:
            serial += 1
            heapq.heappush(queue, (instant + delay, serial, response.serve_token))
            metrics["delayed_clicks"] += 1
        observable = seen and (not lost or clicked)
        predicted = service.estimate(next(cr for c in campaigns for cr in c.creatives
                                        if cr.creative_id == rec.creative_id), policies[slot]).mean
        if observable:
            observed[rec.creative_id, slot] += 1
            true_sum[rec.creative_id, slot] += p
            prediction_sum[rec.creative_id, slot] += predicted
        truth.append(dict(request_id=req.request_id, creative_id=rec.creative_id, slot=slot.value,
                          probability=p, observed=observable, clicked=clicked, delay_ms=delay,
                          seen=seen, lost_beacon=lost))
        predictions.append(dict(creative_id=rec.creative_id, slot=slot.value, mean=predicted))
    flush(BASE_MS + 86_400_000 + RELEASE_AFTER_MS + 1)
    if scenario == 5 and click_tokens:
        token = click_tokens[0]
        body, signature = token.split(".")
        bad = body + "." + ("A" if signature[0] != "A" else "B") + signature[1:]
        invalid_types["bad_signature"] += int(click(bad).judgement.reason.value == "bad_signature")
        orphan = sign_token(ServeClaims("syn:orphan", "syn:orphan-session", SLOT, now[0], now[0]+TOKEN_TTL_MS), service.key)
        click(orphan)
        now[0] += GRACE_MS + 1
        invalid_types["no_serve"] += int(click(orphan).judgement.reason.value == "no_serve")
        now[0] += TOKEN_TTL_MS + 1
        invalid_types["expired"] += int(click(token).judgement.reason.value == "expired")
    service.book.release_expired(now[0])
    final = build_ctr(_final_snapshot(service, now[0], "final"), policies)
    estimates = {(r["creative_id"], AdSlot(r["slot"])): r["alpha"] / (r["alpha"] + r["beta"]) for r in final}
    if policy_name != "thompson":
        estimates = {cr: .08 for cr in observed}
    errors = {cr: abs(estimates.get(cr, .08) - true_sum[cr] / n) / (true_sum[cr] / n)
              for cr, n in observed.items() if n}
    online_error = sum(abs(prediction_sum[cr] - true_sum[cr]) / true_sum[cr] * n
                       for cr, n in observed.items()) / max(1, sum(observed.values()))
    metrics.update(ctr_relative_error=sum(errors[cr]*n for cr, n in observed.items()) / max(1, sum(observed.values())),
        online_ctr_relative_error=online_error,
        ctr_by_creative={cr[0]+"@"+cr[1].value: dict(impressions=observed[cr], true_ctr=true_sum[cr]/observed[cr],
            predicted_ctr=estimates.get(cr, .08), relative_error=err) for cr, err in errors.items()},
        filled=len(truth), valid_impressions=len(service.state.impressed), valid_clicks=len(service.state.clicked),
        settled_mw=sum(r.amount_mw for r in service.book.rows.values() if r.state is ChargeState.CONFIRMED),
        click_regret=None, revenue_regret_mw=None,
        invalid_types=dict(invalid_types), winner_counts=dict(winner_counts),
        exploration_counts={cid: v for (cid, _), v in service.exposure_counts.items()},
        source="local-memory-final-aggregate", comparison="full-day true-CTR reference, same initial candidates/budgets/cap/pacing rules",
        posterior=final)
    if scenario == 1:
        metrics["budget_error"] = abs(metrics["settled_mw"] / campaigns[0].daily_budget_mw - 1)
    if scenario == 2:
        metrics["high_ctr_low_bid_wins"] = winner_counts["syn:quality"] > winner_counts["syn:bidder"]
    if scenario == 3:
        cr = new_creative.creative_id
        count = sum(n for (creative, slot), n in observed.items() if creative == cr)
        error = sum(errors[key]*n for key, n in observed.items() if key[0] == cr) / max(1, count)
        metrics["new_creative_impressions"] = count
        metrics["new_creative_relative_error"] = error
        metrics["new_creative_converged"] = count > 0 and error <= .2
    if scenario == 4:
        metrics["budget_changes_winner_and_price"] = bool(before and after and sum(before)/len(before) > sum(after)/len(after))
        metrics["budget_excluded_then_fallback"] = len(after)
        metrics["prices_before_after"] = [sum(before)/len(before) if before else None, sum(after)/len(after) if after else None]
    metrics["local_pass"] = (metrics["ctr_relative_error"] <= .2 and metrics["invalid_charges"] == 0
        and (scenario != 1 or metrics["budget_error"] <= .1)
        and (scenario != 2 or metrics["high_ctr_low_bid_wins"])
        and (scenario != 3 or metrics["new_creative_converged"])
        and (scenario != 4 or metrics["budget_changes_winner_and_price"])
        and (scenario != 5 or all(invalid_types.values())))
    return dict(scenario=scenario, seed=seed, policy=policy_name, requests=requests, metrics=metrics,
                truth=truth, events=events, predictions=predictions)


def evaluate(scenarios, seeds, requests, root=ROOT):
    """Write separate truth/observations and a three-seed acceptance report, preserving failures."""
    if len(set(seeds)) < 3 or len(set(seeds)) != len(seeds):
        raise ValueError("at least three distinct seeds required")
    if not scenarios or any(s not in range(1, 6) for s in scenarios) or requests < 30:
        raise ValueError("scenarios must be 1..5 and requests >= 30")
    root = Path(root)
    out = root / "out/ad_scenarios"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for scenario in scenarios:
        for seed in seeds:
            for policy in ("thompson", "random", "fixed", "oracle"):
                result = run_policy(scenario, seed, requests, policy)
                name = f"s{scenario}-seed{seed}-{policy}"
                (out / (name + "-truth.json")).write_text(json.dumps(result.pop("truth")), encoding="utf-8")
                (out / (name + "-observed.json")).write_text(json.dumps(dict(
                    events=result.pop("events"), predictions=result.pop("predictions"))), encoding="utf-8")
                rows.append(result)
                log("ad_scenarios", "info", "local scenario measured", scenario=scenario, seed=seed, policy=policy,
                    ctr_error=result["metrics"]["ctr_relative_error"], invalid_charges=result["metrics"]["invalid_charges"])
            group = [r for r in rows if r["scenario"] == scenario and r["seed"] == seed]
            oracle = next(r["metrics"] for r in group if r["policy"] == "oracle")
            for result in group:
                result["metrics"]["click_regret"] = oracle["valid_clicks"] - result["metrics"]["valid_clicks"]
                result["metrics"]["revenue_regret_mw"] = oracle["settled_mw"] - result["metrics"]["settled_mw"]
    report = dict(conclusion=CONCLUSION, scope="local; no Kafka/Spark/gold reconciliation or B handoff claimed",
                  local_pass=all(r["metrics"]["local_pass"] for r in rows if r["policy"] == "thompson"), runs=rows)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics = {}
    for scenario in scenarios:
        runs = [r["metrics"] for r in rows if r["scenario"] == scenario and r["policy"] == "thompson"]
        metrics[f"s{scenario}_ctr_error_max"] = dict(value=max(r["ctr_relative_error"] for r in runs), unit="")
        metrics[f"s{scenario}_online_ctr_error_max"] = dict(value=max(r["online_ctr_relative_error"] for r in runs), unit="")
        metrics[f"s{scenario}_invalid_charges"] = dict(value=sum(r["invalid_charges"] for r in runs), unit="건")
        if scenario == 1:
            metrics["s1_budget_error_max"] = dict(value=max(r["budget_error"] for r in runs), unit="")
        flag = {2: "high_ctr_low_bid_wins", 3: "new_creative_converged", 4: "budget_changes_winner_and_price"}.get(scenario)
        if flag:
            metrics[f"s{scenario}_expected_behavior_passes"] = dict(value=sum(r[flag] for r in runs), unit="건")
    metrics["distinct_seeds"] = dict(value=len(seeds), unit="개")
    write_result("D-08-02", "scenarios", metrics, "python -m tools.ad_scenarios --scenario " +
                 ",".join(map(str, scenarios)) + " --seeds " + ",".join(map(str, seeds)) + f" --requests {requests}", root=root)
    lines = ["# D-08-02 local scenarios", "", CONCLUSION, "", report["scope"], "",
             "CTR is final posterior vs latent marginal; online prequential error is also reported.",
             "CTR error columns for random/fixed/oracle are stored-prior diagnostics, not their ranking-score accuracy. Only Thompson CTR errors are acceptance gates.",
             "Regret is oracle minus policy full-day valid clicks / settled milliwon; signed values can be negative.",
             "The true-CTR oracle has the same initial opportunities, candidates, budgets, frequency cap, exploration and pacing rules. It is a reference, not a proven global optimum.",
             "random uses independent Uniform(0,1) CTR scores; fixed uses slot prior. Budgets, cap and pacing are shared rules.", "",
             "| Scenario | Seed | Policy | Final CTR error | Online CTR error | Budget error | Click regret | Revenue regret mw | Local pass |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        m = r["metrics"]
        lines.append(f'| {r["scenario"]} | {r["seed"]} | {r["policy"]} | {m["ctr_relative_error"]:.4%} | '
                     f'{m["online_ctr_relative_error"]:.4%} | {m.get("budget_error", 0):.4%} | '
                     f'{m["click_regret"]:.3f} | {m["revenue_regret_mw"]:.3f} | {m["local_pass"]} |')
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    """Run selected scenarios; actual gold reconciliation remains an explicit handoff."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="1..5")
    parser.add_argument("--seeds", default="17,29,43")
    parser.add_argument("--requests", type=int, default=12000)
    args = parser.parse_args(argv)
    try:
        scenarios = list(range(1, 6)) if args.scenario == "1..5" else [int(s) for s in args.scenario.split(",")]
        report = evaluate(scenarios, [int(s) for s in args.seeds.split(",")], args.requests)
    except (ValueError, OSError) as exc:
        log("ad_scenarios", "error", "evaluation failed", error_type=type(exc).__name__)
        return 1
    return 0 if report["local_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
