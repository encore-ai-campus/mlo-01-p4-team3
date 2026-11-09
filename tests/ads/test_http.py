"""D-05-03: 실제 MySQL 캠페인과 Django HTTP 경유 계약·공격·admin 검증."""
import json
import math
import secrets
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase

from ads.app.serve import AdService
from ads.domain.model import AdSlot, BillingType, ChargeState, ServeClaims, SlotPolicy
from ads.domain.rules import TOKEN_TTL_MS, reconcile, sign_token, verify_token
from ads.project.composition import load_campaigns, load_policies
from ads.web.models import Campaign, Creative
from ads.web import views

ROOT = Path(__file__).resolve().parents[2]
BASE_MS = int(datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp() * 1000)


class AdHTTPTests(TestCase):
    """API 응답과 내부 판정·원장 상태를 함께 대조한다."""

    def setUp(self):
        self.now = BASE_MS
        self.service = AdService(load_campaigns, load_policies(), secrets.token_bytes(32),
                                 clock=lambda: self.now)
        self.provider = patch.object(views, "get_service", return_value=self.service)
        self.provider.start()
        self.addCleanup(self.provider.stop)
        self.client = Client(enforce_csrf_checks=True)
        self.serial = 0

    def campaign(self, cid="cp", billing="cpc", bid=100000, budget=10000000, **kwargs):
        """필요한 캠페인만 MySQL에 만든다."""
        c = Campaign.objects.create(campaign_id=cid, advertiser_id="adv", name=cid,
            slots=["lobby_banner"], billing=billing, max_bid_mw=bid, daily_budget_mw=budget,
            start_date="2026-11-01", end_date="2026-12-01", **kwargs)
        Creative.objects.create(creative_id=("syn:cr_" + cid[4:]) if cid.startswith("syn:") else "cr_" + cid, campaign=c, title="가상 광고",
                                body="사이트 안 안내", image_path="/static/ads/example.png")
        return c

    def serve(self, request_id=None, session=None, **kwargs):
        """HTTP serve 요청을 보내고 JSON을 받는다."""
        self.serial += 1
        payload = dict(request_id=request_id or f"r{self.serial}", slot="lobby_banner",
                       session_pseudonym=session or f"s{self.serial}", context={})
        payload.update(kwargs)
        response = self.client.post("/ads/v1/serve", json.dumps(payload),
            content_type="application/json", HTTP_X_REQUEST_ID="relay-request")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def event(self, kind, token, **kwargs):
        """HTTP 클릭 또는 비콘을 보내 마지막 판정을 받는다."""
        if kind == "click":
            response = self.client.get("/ads/v1/click", {"t": token}, HTTP_X_REQUEST_ID="relay-event")
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith("/ads/landing/"))
            self.assertFalse(response["Location"].startswith("//"))
        else:
            payload = dict(token=token, visible_ratio=1., visible_ms=1200, occurred_at_ms=self.now)
            payload.update(kwargs)
            response = self.client.post("/ads/v1/impression", json.dumps(payload),
                content_type="text/plain", HTTP_X_REQUEST_ID="relay-event")
            self.assertEqual(response.status_code, 204)
        return self.service.last_result

    def record(self, token):
        """서명된 응답 토큰에서 내부 serve 기록을 찾는다."""
        claims, reason = verify_token(token, self.service.key, self.now)
        self.assertIsNone(reason)
        return self.service.state.serves[claims.serve_id]

    def test_contract_csrf_relay_logging_metrics_and_landing(self):
        self.campaign()
        with self.assertLogs("paldo.ads", level="INFO") as logs:
            filled = self.serve()
            self.assertEqual(filled["creative"]["image_path"], "/static/ads/example__728x90.png")
            result = self.event("impression", filled["serve_token"])
            self.assertEqual(result.judgement.status.value, "valid")
            result = self.event("click", filled["serve_token"])
            self.assertEqual(result.charge_mw, self.record(filled["serve_token"]).price_mw)
        self.assertTrue(any(r.fields["request_id"] == "relay-event" for r in logs.records))
        self.assertTrue(any(r.fields.get("serve_id") for r in logs.records))
        self.assertTrue(any(r.fields.get("charge_id") for r in logs.records))
        self.assertFalse(any(filled["serve_token"] in r.getMessage() for r in logs.records))
        metrics = self.client.get("/metrics").content.decode()
        for name in ("paldo_ad_serve_total", "paldo_ad_serve_duration_seconds_bucket",
                     "paldo_ad_impression_total", "paldo_ad_click_total"):
            self.assertIn(name, metrics)
        self.assertIn('filled="true"', metrics)
        landing = self.client.get(result.redirect_path)
        self.assertEqual(landing.status_code, 200)
        self.assertContains(landing, "가상 광고")

    def test_auction_fixtures_over_http(self):
        cases = json.loads((ROOT / "fixtures/ads/auction_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["case_id"]):
                Campaign.objects.all().delete()
                self.service.responses.clear()
                ranks = {}
                for b in case["bidders"]:
                    c = self.campaign(b["campaign_id"], b["billing"], b["bid_mw"], 10**12)
                    cr = c.creatives.get()
                    cr.creative_id = b["creative_id"]
                    c.creatives.all().delete()
                    cr.save(force_insert=True)
                    ranks[cr.creative_id] = b["rank_ctr"]
                self.service.policies[AdSlot.LOBBY_BANNER] = SlotPolicy(
                    AdSlot.LOBBY_BANNER, case["reserve_ecpm_mw"], .02)
                with patch.object(self.service, "rank_ctr", side_effect=lambda c, cr, p, rng: ranks[cr.creative_id]):
                    out = self.serve(request_id=case["request_id"])
                actual = self.service.auctions[case["request_id"]]
                expected = case["expect"]
                self.assertEqual(out["filled"], actual.winner is not None)
                for key, value in expected.items():
                    got = actual.winner.campaign_id if key == "winner" and actual.winner else (
                        None if key == "winner" else getattr(actual, key))
                    self.assertEqual(got, value, key)
                if "expect_any_winner" in case:
                    self.assertIn(actual.winner.campaign_id, case["expect_any_winner"])

    def test_validity_fixtures_over_http(self):
        cases = json.loads((ROOT / "fixtures/ads/validity_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["case_id"]):
                Campaign.objects.all().delete()
                self.setUp_service()
                self.campaign(billing=case["billing"], bid=case["price_mw"])
                self.service.policies[AdSlot.LOBBY_BANNER] = SlotPolicy(
                    AdSlot.LOBBY_BANNER, case["price_mw"] if case["billing"] == "cpm"
                    else case["price_mw"] * 20, .02)
                tokens = {}
                with patch.object(self.service, "rank_ctr", return_value=.02):
                    for step in case["steps"]:
                        op, label = step["op"], step["token"]
                        if op == "serve":
                            self.now = BASE_MS
                            tokens[label] = self.serve(session=step["session"])["serve_token"]
                            continue
                        if label == "t_unserved":
                            tokens.setdefault(label, sign_token(ServeClaims("missing", "s", AdSlot.LOBBY_BANNER,
                                BASE_MS, BASE_MS + TOKEN_TTL_MS), self.service.key))
                        token = tokens[label.split(":")[-1]]
                        if label.startswith("forged:"):
                            token += "x"
                        self.now = BASE_MS + step.get("judge_ms", step.get("recv_ms", step["at_ms"]))
                        result = self.event(op, token,
                            visible_ratio=step.get("visible_ratio", 1), visible_ms=step.get("visible_ms", 1200),
                            occurred_at_ms=BASE_MS + step["at_ms"])
                        j = result.judgement
                        actual = j.status.value + (":" + j.reason.value if j.reason else "")
                        self.assertEqual(actual, step["expect"])
                        if "expect_implied" in step:
                            self.assertEqual(j.implied_impression, step["expect_implied"])
                        if "expect_suspect" in step:
                            self.assertEqual(j.suspect, step["expect_suspect"])
                total = sum(r.amount_mw for r in self.service.book.rows.values() if r.state is ChargeState.CONFIRMED)
                self.assertEqual(total, case["expect_confirmed_mw"])

    def setUp_service(self):
        """픽스처 사이 메모리 원장·세션·판정을 분리한다."""
        self.now = BASE_MS
        self.service = AdService(load_campaigns, load_policies(), secrets.token_bytes(32),
                                 clock=lambda: self.now)
        views.get_service.return_value = self.service

    def test_budget_fixtures_http_charges_and_maintenance(self):
        cases = json.loads((ROOT / "fixtures/ads/budget_cases.json").read_text(encoding="utf-8"))
        case = cases[0]
        self.campaign(bid=case["price_mw"], budget=case["daily_budget_mw"])
        self.service.policies[AdSlot.LOBBY_BANNER] = SlotPolicy(AdSlot.LOBBY_BANNER, 1000000, .02)
        tokens, records = {}, {}
        with patch.object(self.service, "rank_ctr", return_value=.02):
            for step in case["steps"]:
                op = step["op"]
                if op == "reserve":
                    self.now = BASE_MS + step["at_ms"]
                    out = self.serve(request_id=step["serve"])
                    self.assertEqual(out["filled"], step["expect"])
                    if out["filled"]:
                        tokens[step["serve"]] = out["serve_token"]
                        records[step["serve"]] = self.record(out["serve_token"])
                elif op == "confirm":
                    result = self.event("click", tokens[step["serve"]])
                    self.assertEqual(bool(result.charge_id), step["expect"])
                elif op == "release":
                    self.now = BASE_MS + step["now_ms"]
                    before = {k for k, r in self.service.book.rows.items() if r.state is ChargeState.RELEASED}
                    self.serve(request_id=f"tick-{step['now_ms']}", slot="review_side")
                    after = {k for k, r in self.service.book.rows.items() if r.state is ChargeState.RELEASED}
                    expected = {records[s.split(":")[0]].serve_id + ":click" for s in step["expect_released"]}
                    self.assertEqual(after - before, expected)
                else:
                    # 환입·대사는 HTTP 계약이 없다. HTTP로 만든 원장에 기존 도메인 연산만 적용한다.
                    cid = records[step["serve"]].serve_id + ":click"
                    refund = self.service.book.refund(cid, step["event"])
                    self.assertEqual(refund.amount_mw, step["expect_amount_mw"])
        self.assertEqual(self.service.book.committed_mw("cp", "2026-11-06"), case["expect_committed_mw"])
        case = cases[1]
        self.setUp_service()
        campaign = Campaign.objects.get(pk="cp")
        campaign.daily_budget_mw = sum(amount for amount in case["ledger"].values() if amount > 0)
        charge_ids = {}
        with patch.object(self.service, "rank_ctr", return_value=.02):
            for label, amount in case["ledger"].items():
                if label.endswith(":refund"):
                    original = charge_ids[label.removesuffix(":refund")]
                    refund = self.service.book.refund(original, "reconcile-refund")
                    self.assertIsNotNone(refund)
                    charge_ids[label] = refund.charge_id
                else:
                    campaign.max_bid_mw = amount
                    campaign.save()
                    self.service.policies[AdSlot.LOBBY_BANNER] = SlotPolicy(
                        AdSlot.LOBBY_BANNER, amount * 20, .02)
                    out = self.serve(request_id=label)
                    self.assertTrue(out["filled"])
                    charge_ids[label] = self.event("click", out["serve_token"]).charge_id
        self.assertEqual(len(self.service.book.rows), len(case["ledger"]))
        ledger = {}
        for label, cid in charge_ids.items():
            row = self.service.book.rows[cid]
            self.assertEqual(row.state, ChargeState.CONFIRMED)
            ledger[label] = row.amount_mw
        self.assertEqual(ledger, case["ledger"])
        self.assertEqual(reconcile(ledger, case["delta"]), case["expect"])

    def test_idempotency_and_request_collision(self):
        self.campaign()
        first = self.serve(request_id="same")
        self.assertEqual(self.serve(request_id="same", session="s1"), first)
        self.assertEqual(len(self.service.book.rows), 1)
        response = self.client.post("/ads/v1/serve", json.dumps(dict(request_id="same", slot="lobby_banner",
            session_pseudonym="different", context={})), content_type="application/json")
        self.assertEqual(response.status_code, 409)

    def test_source_targeting_frequency_and_load_test(self):
        self.campaign()
        self.assertFalse(self.serve(source="synthetic")["filled"])
        self.campaign(cid="syn:cp")
        out = self.serve(source="synthetic")
        self.assertTrue(out["creative"]["creative_id"].startswith("syn:"))
        for _ in range(3):
            self.assertTrue(self.serve(session="capped")["filled"])
        self.assertFalse(self.serve(session="capped")["filled"])
        out = self.serve(load_test=True)
        result = self.event("click", out["serve_token"])
        self.assertEqual(result.judgement.reason.value, "load_test")
        c = Campaign.objects.get(pk="cp")
        c.targeting = {"returning": True, "outcomes": ["lose"], "hours": [18], "preset_ids": ["standard"]}
        c.save()
        self.assertFalse(self.serve()["filled"])
        self.assertTrue(self.serve(context=dict(returning=True, outcome="lose", hour=18, preset_id="standard"))["filled"])

    def test_untrusted_input_and_never_external_redirect(self):
        self.campaign()
        for payload in ({}, [], {"token": "x", "visible_ratio": math.nan, "visible_ms": 1000, "occurred_at_ms": 0},
                        {"token": "x", "visible_ratio": 1, "visible_ms": True, "occurred_at_ms": 0}):
            with self.subTest(payload=payload):
                r = self.client.post("/ads/v1/impression", json.dumps(payload), content_type="application/json")
                self.assertEqual(r.status_code, 400)
        for token in ("", "https://evil.example", "x.y", "malformed"):
            r = self.client.get("/ads/v1/click", {"t": token})
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r["Location"], "/ads/landing/unavailable")
        cr = Creative.objects.get()
        cr.image_path = "//evil.example/a.png"
        with self.assertRaises(ValidationError):
            cr.full_clean()

    def test_image_replacement_uses_data_without_code_change(self):
        c = self.campaign()
        a = self.serve()
        cr = c.creatives.get()
        cr.image_path = "/static/ads/replaced.png"
        cr.save()
        b = self.serve()
        self.assertNotEqual(a["creative"]["image_path"], b["creative"]["image_path"])
        self.assertEqual(b["creative"]["image_path"], "/static/ads/replaced__728x90.png")
        cr.title = '<script>alert("x")</script>'
        cr.save()
        self.assertNotIn(b"<script>", self.client.get("/ads/landing/" + cr.pk).content)

    def test_admin_login_create_pause_and_budget_edit(self):
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        admin = get_user_model().objects.create_superuser("operator", password=secrets.token_urlsafe(24))
        staff = Client()
        staff.force_login(admin)
        data = dict(campaign_id="admin", advertiser_id="adv", name="admin",
            slots='["lobby_banner"]', targeting="{}", billing="cpc", max_bid_mw=100000,
            daily_budget_mw=10000000, start_date="2026-11-01", end_date="2026-12-01", status="active",
            **{"creatives-TOTAL_FORMS": "1", "creatives-INITIAL_FORMS": "0",
               "creatives-MIN_NUM_FORMS": "0", "creatives-MAX_NUM_FORMS": "1000",
               "creatives-0-creative_id": "admin_cr", "creatives-0-title": "생성",
               "creatives-0-body": "설명", "creatives-0-image_path": "/static/ads/new.png"})
        r = staff.post("/admin/ads_web/campaign/add/", data)
        self.assertEqual(r.status_code, 302, r.content)
        self.assertTrue(self.serve()["filled"])
        data.update(status="paused", **{"creatives-INITIAL_FORMS": "1",
            "creatives-0-campaign": "admin"})
        r = staff.post("/admin/ads_web/campaign/admin/change/", data)
        self.assertEqual(r.status_code, 302, r.content)
        self.assertFalse(self.serve()["filled"])
        data.update(status="active", daily_budget_mw=0)
        self.assertEqual(staff.post("/admin/ads_web/campaign/admin/change/", data).status_code, 200)
        saved = Campaign.objects.get(pk="admin")
        self.assertEqual((saved.status, saved.daily_budget_mw), ("paused", 10000000))
        self.assertFalse(self.serve()["filled"])
        data["daily_budget_mw"] = 200000
        self.assertEqual(staff.post("/admin/ads_web/campaign/admin/change/", data).status_code, 302)
        self.assertEqual(Campaign.objects.get(pk="admin").daily_budget_mw, 200000)
        self.assertTrue(self.serve()["filled"])

    def test_admin_rejects_cpc_budget_below_max_charge_but_allows_cpm(self):
        admin = get_user_model().objects.create_superuser("budget_operator", password=secrets.token_urlsafe(24))
        staff = Client()
        staff.force_login(admin)
        data = dict(campaign_id="registration", advertiser_id="adv", name="registration",
            slots='["lobby_banner"]', targeting="{}", billing="cpc", max_bid_mw=300000,
            daily_budget_mw=100000, start_date="2026-11-01", end_date="2026-12-01", status="active",
            **{"creatives-TOTAL_FORMS": "0", "creatives-INITIAL_FORMS": "0",
               "creatives-MIN_NUM_FORMS": "0", "creatives-MAX_NUM_FORMS": "1000"})
        response = staff.post("/admin/ads_web/campaign/add/", data)
        self.assertEqual(response.status_code, 200)
        self.assertIn("daily_budget_mw", response.context["adminform"].form.errors)
        self.assertFalse(Campaign.objects.filter(pk="registration").exists())
        data["billing"] = "cpm"
        response = staff.post("/admin/ads_web/campaign/add/", data)
        self.assertEqual(response.status_code, 302, response.content)
        self.assertEqual(Campaign.objects.get(pk="registration").daily_budget_mw, 100000)

    def test_budget_filter_records_max_bid_reason_even_when_auction_price_fits(self):
        campaign = self.campaign(bid=100000, budget=400000)
        self.service.policies[AdSlot.LOBBY_BANNER] = SlotPolicy(AdSlot.LOBBY_BANNER, 1000000, .02)
        with patch.object(self.service, "rank_ctr", return_value=.02):
            first = self.serve(request_id="initial")
            record = self.record(first["serve_token"])
            self.assertEqual(record.price_mw, 50000)
            self.assertEqual(self.service.book.committed_mw("cp", "2026-11-06"), 50000)
            self.event("click", first["serve_token"])
            # Five confirmed 50-won charges plus one reservation leave 100 won.
            for i in range(5):
                out = self.serve(request_id=f"spend-{i}")
                self.assertTrue(out["filled"])
                if i < 4:
                    self.event("click", out["serve_token"])
            self.assertEqual(self.service.book.committed_mw("cp", "2026-11-06"), 300000)
            campaign.max_bid_mw = 300000
            campaign.full_clean()
            campaign.save()
            before = len(self.service.book.rows)
            with self.assertLogs("paldo.ads", level="INFO") as logs:
                out = self.serve(request_id="budget-filtered")
        self.assertFalse(out["filled"])
        self.assertEqual(len(self.service.book.rows), before)
        candidates = [c for log in logs.records for c in log.fields.get("candidates", [])]
        self.assertEqual([(c["campaign_id"], c["filtered"]) for c in candidates], [("cp", "budget")])
        self.assertEqual(candidates[0]["bid_mw"], 300000)
        self.assertEqual(self.serve(request_id="budget-filtered", session="s7"), out)

    def test_reservation_rejection_records_budget_without_ledger_row(self):
        self.campaign()
        # Simulate a competing reservation between the filter and atomic reserve.
        with patch.object(self.service.book, "reserve", return_value=False), self.assertLogs("paldo.ads") as logs:
            out = self.serve()
        self.assertFalse(out["filled"])
        self.assertEqual(self.service.book.rows, {})
        self.assertEqual(self.service.state.serves, {})
        candidates = [c for log in logs.records for c in log.fields.get("candidates", [])]
        self.assertEqual([(c["campaign_id"], c["filtered"]) for c in candidates], [("cp", "budget")])

    def test_concurrent_http_requests_cannot_overspend_or_double_charge(self):
        c = self.campaign(bid=50000, budget=120000)
        campaigns = load_campaigns()
        self.service.campaigns = lambda: campaigns
        self.service.policies[AdSlot.LOBBY_BANNER] = SlotPolicy(AdSlot.LOBBY_BANNER, 1000000, .02)
        def request(i):
            payload = dict(request_id=f"parallel-{i}", slot="lobby_banner",
                           session_pseudonym=f"parallel-{i}", context={})
            return Client().post("/ads/v1/serve", json.dumps(payload), content_type="application/json").json()
        with patch.object(self.service, "rank_ctr", return_value=.02), self.assertLogs("paldo.ads"):
            with ThreadPoolExecutor(max_workers=16) as pool:
                outputs = list(pool.map(request, range(100)))
            winners = [out for out in outputs if out["filled"]]
            self.assertEqual(len(winners), 2)
            token = winners[0]["serve_token"]
            with ThreadPoolExecutor(max_workers=16) as pool:
                codes = list(pool.map(lambda _: Client().get("/ads/v1/click", {"t": token}).status_code,
                                      range(100)))
        self.assertEqual(set(codes), {302})
        self.assertEqual(sum(r.state is ChargeState.CONFIRMED for r in self.service.book.rows.values()), 1)
        self.assertEqual(self.service.book.committed_mw("cp", "2026-11-06"), 100000)

    def test_validation_admin_csrf_and_json_content_types(self):
        self.campaign()
        cr = Creative.objects.get()
        cr.full_clean()
        for context in ([], {"hour": True}, {"returning": "false"}, {"outcome": "other"}, {"private": "x"}):
            payload = dict(request_id="bad", slot="lobby_banner", session_pseudonym="s", context=context)
            r = self.client.post("/ads/v1/serve", json.dumps(payload), content_type="application/json")
            self.assertEqual(r.status_code, 400)
        for method, path in (("get", "/ads/v1/serve"), ("get", "/ads/v1/impression"),
                             ("post", "/ads/v1/click")):
            self.assertEqual(getattr(self.client, method)(path).status_code, 405)
        for content_type in ("application/json", "text/plain", "application/octet-stream"):
            token = self.serve()["serve_token"]
            payload = dict(token=token, visible_ratio=.5, visible_ms=1000, occurred_at_ms=0)
            self.assertEqual(self.client.post("/ads/v1/impression", json.dumps(payload),
                                             content_type=content_type).status_code, 204)
        self.assertEqual(self.client.post("/admin/login/", {"username": "nobody"}).status_code, 403)

    def test_pending_uses_first_server_receipt_and_stays_invalid(self):
        token = sign_token(ServeClaims("missing", "s", AdSlot.LOBBY_BANNER, BASE_MS, BASE_MS + 500),
                           self.service.key)
        self.now = BASE_MS + 100
        self.assertEqual(self.event("impression", token).judgement.status.value, "pending")
        self.now = BASE_MS + 121000
        for _ in range(2):
            self.assertEqual(self.event("impression", token).judgement.reason.value, "no_serve")

    def test_same_asset_path_can_be_overwritten_without_server_change(self):
        from tempfile import TemporaryDirectory
        from django.test import override_settings
        source = ROOT / "content/images"
        # E의 실제 임시 PNG 두 장으로 정적 파일 덮어쓰기를 재현한다.
        pngs = list((source / "ad").glob("*__728x90.png"))
        self.assertGreaterEqual(len(pngs), 2)
        self.campaign()
        with TemporaryDirectory() as folder, override_settings(STATIC_ROOT=folder):
            image = Path(folder) / "ads/example__728x90.png"
            image.parent.mkdir()
            image.write_bytes(pngs[0].read_bytes())
            first_bytes = image.read_bytes()
            a = self.serve()["creative"]
            image.write_bytes(pngs[1].read_bytes())
            b = self.serve()["creative"]
            self.assertNotEqual(first_bytes, image.read_bytes())
            self.assertEqual(a["image_path"], b["image_path"])
            self.assertEqual(b["image_path"], "/static/ads/example__728x90.png")

    def test_current_contract_has_three_slots_and_rejects_removed_wait_slot(self):
        self.assertEqual({slot.value for slot in AdSlot},
                         {"lobby_banner", "result_banner", "review_side"})
        for slot in ("llm_wait", "meeting", "vote", "night"):
            payload = dict(request_id="removed-" + slot, slot=slot, session_pseudonym="s", context={})
            self.assertEqual(self.client.post("/ads/v1/serve", json.dumps(payload),
                            content_type="application/json").status_code, 400)

    def test_seed_campaign_images_match_manifest_for_every_allowed_slot(self):
        from django.core.management import call_command
        call_command("load_ad_fixtures", verbosity=0)
        call_command("load_ad_fixtures", verbosity=0)
        self.assertEqual(Campaign.objects.count(), 4)
        manifest = json.loads((ROOT / "content/images/manifest.json").read_text(encoding="utf-8"))
        urls = {item["url"] for item in manifest}
        for slot, context in (("lobby_banner", {}),
                              ("result_banner", {"outcome": "lose"}),
                              ("review_side", {})):
            out = self.serve(slot=slot, context=context)
            self.assertTrue(out["filled"])
            self.assertIn(out["creative"]["image_path"], urls)
