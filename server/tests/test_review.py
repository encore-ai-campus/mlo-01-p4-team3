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
        self.assertEqual(set(output), {"game_id", "result", "roles", "secrets", "timeline", "explanation_job_id", "clues_gained"})
        self.assertEqual(output["roles"], {"blacksmith": "possessed", "jumo": "villager"})
        self.assertEqual(output["secrets"], {"jumo": "private-secret"})
        self.assertEqual(output["clues_gained"], ["this-clue"])
        self.assertIsNone(output["explanation_job_id"])
        self.assertEqual(output["timeline"], [{"speech_id": speech.speech_id, "actor": speech.speaker,
                                              "reason_codes": [], "evidence": to_jsonable(speech.evidence)}
                                             for speech in self.document["state"].public.speeches])
        self.assertNotIn("claim_truths", output)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        self.assertEqual(self.document, before)
