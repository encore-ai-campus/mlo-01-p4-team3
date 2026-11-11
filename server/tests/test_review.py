"""기본 복기의 종료·소유권·공개 JSON 범위를 검증한다."""
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from engine.domain.model import ClaimTruth, ClueRecord, Role
from engine.domain.serde import to_jsonable
from server.app.dto import StartGameInput
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


class ReviewAPI(TestCase):
    """진행 중에는 비밀이 보이지 않고 종료 후에도 허용 DTO 밖 자료는 보이지 않는다."""

    def setUp(self):
        """독립 계정·판을 만들고 테스트용 포트를 주입한다."""
        self.user = User.objects.create_user(username="review-owner")
        self.register_profile = install_test_profiles(self)
        self.profile = self.register_profile(self.user)
        self.client.force_login(self.user)
        self.service = make_service()
        configure_game_service(self.service)
        self.addCleanup(configure_game_service, None)
        self.pid = str(self.profile.player_pseudonym)
        game = self.service.start(StartGameInput(self.pid, "base6", "test-session", "f"), self.pid, True)
        self.gid = game.view.game_id
        self.path = f"/games/{self.gid}/review"
        self.document = self.service.repository.docs[self.gid]
        self.document["truth"].roles = {"blacksmith": Role.POSSESSED, "jumo": Role.VILLAGER}
        self.document["truth"].secrets = {"jumo": "private-secret"}
        self.document["truth"].claim_truths = {"private-claim": ClaimTruth("private-claim", False, True)}

    def finish(self):
        """녹화 판의 종료 상태와 이 판·다른 판의 진척을 준비한다."""
        end = deepcopy(self.service.engine.views[-1])
        self.document["state"] = replace(self.document["state"], result=end.result, phase=end.phase,
                                         public=end.public, state_version=end.state_version)
        progress = self.service.progress.items[self.pid]
        progress.clues = {"this-clue": ClueRecord("this-clue", self.gid, 2),
                          "other-clue": ClueRecord("other-clue", "other-game", 1)}

    def test_auth_and_ownership_precede_finished_check(self):
        """남의 판은 종료 여부와 무관하게 404이며 비로그인 표현별 규칙을 유지한다."""
        other = User.objects.create_user(username="review-other")
        self.register_profile(other)
        self.client.force_login(other)
        for finished in (False, True):
            if finished:
                self.finish()
            response = self.client.get(self.path, HTTP_ACCEPT="application/json")
            self.assertEqual(response.status_code, 404)
            self.assertNotIn(b"private-secret", response.content)
        self.client.logout()
        self.assertEqual(self.client.get(self.path, HTTP_ACCEPT="application/json").status_code, 401)
        self.assertEqual(self.client.get(self.path)["Location"], "/accounts/login")
        self.assertEqual(self.client.get(self.path, HTTP_HX_REQUEST="true")["HX-Redirect"], "/accounts/login")

    def test_unfinished_review_exposes_no_truth(self):
        """종료 전에는 어떤 요청 헤더에서도 역할과 비밀을 반환하지 않는다."""
        with patch.object(self.service.progress, "load", side_effect=AssertionError("must finish first")):
            for headers in ({}, {"HTTP_ACCEPT": "application/json"}, {"HTTP_HX_REQUEST": "true"}):
                response = self.client.get(self.path, **headers)
                self.assertEqual(response.status_code, 403)
                for hidden in (b"private-secret", b"private-claim", b"roles", b"claim_truths"):
                    self.assertNotIn(hidden, response.content)

    def test_finished_review_matches_dto_and_does_not_change_game(self):
        """공개 DTO만 반환하고 이 판의 단서만 고르며 이유 코드를 추정하지 않는다."""
        self.finish()
        before = deepcopy(self.document)
        response = self.client.get(self.path, HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 200)
        output = response.json()
        self.assertEqual(set(output), {"game_id", "result", "roles", "secrets", "timeline", "explanation_job_id", "clues_gained", "win_prob"})
        self.assertEqual(output["roles"], {"blacksmith": "possessed", "jumo": "villager"})
        self.assertEqual(output["secrets"], {"jumo": "private-secret"})
        self.assertEqual(output["clues_gained"], ["this-clue"])
        self.assertIsNone(output["explanation_job_id"])
        # 녹화 엔진은 decision 이벤트를 내지 않는다: 발언에서 이유를 추정해 채우지 않고 빈 목록이다.
        self.assertEqual(output["timeline"], [])
        self.assertNotIn("claim_truths", output)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.document, before)

    def test_timeline_comes_from_decision_events_with_template_text(self):
        """아웃박스의 NPC decision 이벤트가 결정 단위 항목이 되고 템플릿 해설 문장이 이름·조사로 채워진다(E-10-10)."""
        self.finish()
        speech = self.document["state"].public.speeches[0]
        version = self.document["state"].state_version
        template = self.document["outbox"][0]

        def envelope(event_type, payload):
            return replace(template, event_type=event_type, state_version=version, day=1, phase="round1",
                           payload=payload)
        self.service.repository._append(self.document, [
            envelope("decision", {"actor": "mudang", "snapshot_id": "p", "kind": "suspect", "target": "jumo",
                                  "reason_codes": ["scripted"], "evidence": [], "policy_version": "player"}),
            envelope("decision", {"actor": speech.speaker, "snapshot_id": "s1", "kind": speech.kind.value,
                                  "target": "jumo", "reason_codes": ["max_suspicion"], "evidence": [],
                                  "policy_version": "rule_v0"}),
            envelope("speech_made", {"speech_id": speech.speech_id}),
            envelope("decision", {"actor": "jumo", "snapshot_id": "s2", "kind": "vote", "target": "blacksmith",
                                  "reason_codes": ["model_estimate"], "evidence": [], "policy_version": "m-fallback"}),
        ])
        self.service.review_lines = {"rv_max_suspicion": ["{actor}은(는) {target}을(를) 골랐다"],
                                     "rv_model_estimate": ["{actor}은(는) {target}에게 표를 던졌다"]}
        self.addCleanup(setattr, self.service, "review_lines", None)
        before = deepcopy(self.document)
        output = self.client.get(self.path, HTTP_ACCEPT="application/json").json()
        self.assertEqual(self.document, before)
        talk, vote = output["timeline"]
        self.assertEqual((talk["actor"], talk["kind"], talk["speech_id"], talk["target_npc"], talk["phrase_id"]),
                         (speech.speaker, speech.kind.value, speech.speech_id, "jumo", "rv_max_suspicion"))
        self.assertTrue(talk["text"].endswith("주모를 골랐다"))
        self.assertEqual((vote["kind"], vote["speech_id"], vote["target_npc"], vote["policy_version"]),
                         ("vote", None, "blacksmith", "m-fallback"))
        self.assertIn("표를 던졌다", vote["text"])
        self.assertIsNone(vote["vote_explain"])
        html = self.client.get(self.path, HTTP_HX_REQUEST="true").content.decode()
        self.assertIn("주모를 골랐다", html)
        self.assertIn("max_suspicion", html)
        self.assertNotIn("win_prob", html)

    def test_timeline_survives_only_content_lookup_failure(self):
        """판 콘텐츠를 못 읽는 실패(KeyError·RuntimeError)는 이름 없이 진행하고, 그 밖의 오류는 숨기지 않는다."""
        self.finish()
        template = self.document["outbox"][0]
        self.service.repository._append(self.document, [replace(
            template, event_type="decision", state_version=self.document["state"].state_version, day=1,
            phase="vote", payload={"actor": "jumo", "snapshot_id": "s", "kind": "vote", "target": "blacksmith",
                                   "reason_codes": ["model_estimate"], "evidence": [], "policy_version": "m"})])
        self.service.review_lines = {"rv_model_estimate": ["{actor}은(는) {target}에게 표"]}
        self.addCleanup(setattr, self.service, "review_lines", None)
        original = self.service.dependencies
        self.addCleanup(setattr, self.service, "dependencies", original)
        for error in (KeyError("v9"), RuntimeError("content_version_unavailable")):
            def broken(meta, error=error):
                raise error
            self.service.dependencies = broken
            items = self.service._review_timeline(self.document["state"])
            self.assertEqual([i.text for i in items], ["jumo은 blacksmith에게 표"])

        def buggy(meta):
            raise ValueError("bug")
        self.service.dependencies = buggy
        with self.assertRaises(ValueError):
            self.service._review_timeline(self.document["state"])



    def test_win_probability_is_connected_to_owned_finished_review_and_graph_stays_hidden(self):
        """종료 후 모델 확률은 JSON으로 반환하고 D3의 화면 숨김을 적용한다."""
        self.finish()
        import math
        from server.infra.win_probability import WinProbabilityModel

        meta = self.document["state"].meta
        template = self.document["outbox"][0]
        self.service.repository._append(self.document, [
            replace(template, event_type="observation", day=1, phase="round1", payload={"snapshot": {
                "public": {"locks": [], "confessions": [], "disappearances": []}, "private": {}}}),
            replace(template, event_type="game_ended", day=1, phase="ended", payload={"winner": "village"}),
        ])
        before = deepcopy(self.document)
        expected = [dict(day=1, probability=.65)]
        self.service.win_probability = WinProbabilityModel(dict(
            format="win-logistic-v1", feature_version="win-public-v1",
            coefficients={"day": 0}, intercept=math.log(.65 / .35),
            versions={"rule_version": [meta.rule_version], "content_version": [meta.content_version],
                      "preset_id": [meta.preset.preset_id]},
            evaluation={"test_games": 10, "model": {"predicted_mean": .65, "actual_rate": .5}},
        ), "test-win-model")
        self.addCleanup(setattr, self.service, 'win_probability', None)
        response = self.client.get(self.path, HTTP_ACCEPT="application/json")
        self.assertEqual(response.json()['win_prob'], expected)
        self.assertEqual(self.document, before)
        html = self.client.get(self.path, HTTP_HX_REQUEST="true").content.decode()
        self.assertNotIn('win_prob', html)

    def test_unfinished_review_does_not_call_win_probability_model(self):
        """종료 전 거부는 모델 호출보다 먼저 실행한다."""
        def fail(inp, meta):
            raise AssertionError('must finish before predicting')
        self.service.win_probability = fail
        self.addCleanup(setattr, self.service, 'win_probability', None)
        self.assertEqual(self.client.get(self.path, HTTP_ACCEPT="application/json").status_code, 403)
