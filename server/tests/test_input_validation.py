"""저장소 연결 전에 확인할 수 있는 입력 계약 경계를 검사한다."""
from unittest import TestCase
from uuid import uuid4

from engine.domain.serde import SchemaError
from server.api.input_validation import parse_game_input
from server.app.dto import ResolveUtteranceInput, SaveNotebookInput, SubmitSurveyInput


class DeferredApiInputTests(TestCase):
    """API 입력 변환에서 범위와 필수 필드를 검증한다."""

    def test_notebook_bounds(self):
        """의심도·확신도 경계는 허용하고 범위를 벗어난 수를 거부한다."""
        data = {"game_id": "g1", "entries": {"a": 0, "b": 100}, "vote_confidence": 0}
        self.assertEqual(parse_game_input(SaveNotebookInput, data).entries, data["entries"])
        for invalid in ({"entries": {"a": -1}}, {"entries": {"a": 101}}, {"vote_confidence": 101}):
            with self.subTest(invalid=invalid), self.assertRaises(SchemaError):
                parse_game_input(SaveNotebookInput, {**data, **invalid})

    def test_survey_bounds(self):
        """점수 1과 5는 허용하고 세 점수 각각의 범위 위반을 거부한다."""
        data = {"game_id": "g1", "fun": 1, "unfair": 5, "npc_plausible": 3}
        self.assertEqual(parse_game_input(SubmitSurveyInput, data).unfair, 5)
        for field in ("fun", "unfair", "npc_plausible"):
            for invalid in (0, 6):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(SchemaError):
                    parse_game_input(SubmitSurveyInput, {**data, field: invalid})

    def test_reaction_fields(self):
        """카드 실행·수정·재입력의 필수 값과 취소에 필요한 값의 차이를 확인한다."""
        base = {"game_id": "g1", "attempt_id": "a1"}
        execute = {"command_id": str(uuid4()), "expected_state_version": 0}
        for reaction, fields in (("confirm", {**execute, "chosen_index": 0}),
                                 ("edit", {**execute, "edited_command": {"kind": "advance", "actor": "mudang"}}),
                                 ("retry", {"retry_text": "다시 묻는다"}), ("cancel", {})):
            data = {**base, "reaction": reaction, **fields}
            with self.subTest(reaction=reaction):
                self.assertEqual(parse_game_input(ResolveUtteranceInput, data).reaction.value, reaction)
            for name in fields:
                with self.subTest(reaction=reaction, missing=name), self.assertRaises(SchemaError):
                    parse_game_input(ResolveUtteranceInput, {key: value for key, value in data.items() if key != name})
        for invalid in ({"command_id": "not-a-uuid"}, {"expected_state_version": -1}, {"chosen_index": -1}):
            with self.subTest(invalid=invalid), self.assertRaises(SchemaError):
                parse_game_input(ResolveUtteranceInput,
                                 {**base, **execute, "reaction": "confirm", "chosen_index": 0, **invalid})
