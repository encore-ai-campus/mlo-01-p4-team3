"""복기 해설 LLM 요약 도메인 규칙(E-10-01): 항목 선정·요청 모양·응답 해석·문장 검사·템플릿 대체."""
import json
import unittest

from engine.domain.observation import ReasonCode
from llm import review_narration as rn
from llm.validator import Lexicon

LEX = Lexicon(places={"우물가": ("우물가",), "서낭당": ("서낭당",), "주막": ("주막",)},
              names={n: (n,) for n in ("주모", "훈장", "과부", "머슴", "약초꾼")})


def item(item_id, kind, code, *, day=1, actor="jumo", target=None, victim=None, executor=None, reaction=None,
         evidence=(), phase="vote", template="템플릿 문장."):
    return rn.NarrationItem(item_id, day, phase, actor, kind, code, target=target, victim=victim, executor=executor,
                            reaction=reaction, evidence=tuple(evidence), template=template)


def block(items, name="주모", npc="jumo", role="마을 사람"):
    return rn.NpcBlock(npc, name, role, "", list(items))


def reply(block_sentences):
    return json.dumps({"npcs": [{"npc": npc, "sentences": [{"cite": c, "text": t} for c, t in rows]}
                                for npc, rows in block_sentences]}, ensure_ascii=False)


class ReasonDescriptions(unittest.TestCase):
    def test_every_reason_code_has_one_description(self):
        self.assertEqual(set(rn.REASON_DESC), {c.value for c in ReasonCode})
        self.assertTrue(all(v and not v.endswith(".") for v in rn.REASON_DESC.values()))


class SelectItems(unittest.TestCase):
    def test_votes_change_point_and_last_plus_latest_talk(self):
        items = [item("d1", "vote", "model_estimate", target="훈장"),
                 item("d5", "suspect", "rule_suspect", target="과부", phase="round1"),
                 item("d9", "vote", "model_estimate", day=2, target="과부"),
                 item("d12", "vote", "model_estimate", day=3, target="머슴")]
        picked = rn.select_items(items, possessed=set())["jumo"]
        self.assertEqual([i.item_id for i in picked], ["d5", "d9", "d12"])

    def test_possessed_gets_night_plan_and_empty_npc_is_skipped(self):
        items = [item("d3", "vote", "frame_suspected", actor="gwabu", target="머슴"),
                 item("d4", "night_plan", "night_accuser", actor="gwabu", victim="주모", phase="night"),
                 item("d6", "suspect", "rule_suspect", actor="gwabu", target="훈장")]
        out = rn.select_items(items, possessed={"gwabu"})
        self.assertEqual([i.item_id for i in out["gwabu"]], ["d3", "d4"])
        self.assertNotIn("jumo", out)

    def test_game_budget_drops_non_last_votes_first(self):
        items = []
        for n, actor in enumerate(("a", "b", "c", "d", "e")):
            base = n * 10
            items += [item(f"d{base + 1}", "vote", "model_estimate", actor=actor, target="x"),
                      item(f"d{base + 2}", "suspect", "rule_suspect", actor=actor, target="y", day=2),
                      item(f"d{base + 3}", "vote", "model_estimate", actor=actor, target="z", day=3)]
        out = rn.select_items(items, possessed=set())
        self.assertLessEqual(sum(len(v) for v in out.values()), rn.MAX_GAME_ITEMS)
        for picks in out.values():
            self.assertEqual(picks[-1].kind, "vote")          # 마지막 투표는 남는다


class Request(unittest.TestCase):
    def test_schema_pins_npc_and_cites_and_prompt_has_no_template(self):
        b = block([item("d1", "vote", "model_estimate", target="훈장", template="비밀 템플릿 문장.")])
        body = rn.request_body([b], "마을 승리", 7)
        schema = body["response_format"]["json_schema"]["schema"]["properties"]["npcs"]
        npc = schema["prefixItems"][0]
        self.assertEqual(npc["properties"]["npc"], {"const": "jumo"})
        sent = npc["properties"]["sentences"]["prefixItems"][0]["properties"]
        self.assertEqual(sent["cite"], {"const": "d1"})
        self.assertTrue(sent["text"]["pattern"].startswith("^주모는 "))
        text = json.dumps(body["messages"], ensure_ascii=False)
        self.assertNotIn("비밀 템플릿 문장", text)
        self.assertIn("훈장에게 표를 던졌다", text)
        self.assertEqual(body["seed"], 7)

    def test_action_phrase_and_josa(self):
        self.assertEqual(rn.topic_josa("훈장"), "은")
        self.assertEqual(rn.topic_josa("주모"), "는")
        night = item("d2", "night_plan", "seeded_executor", actor="gwabu", executor="머슴", victim="주모")
        self.assertEqual(rn.action_phrase(night, "과부"), "그날 밤 머슴을 움직여 주모를 노렸다")
        self.assertEqual(rn.action_phrase(item("d3", "react", "react_evade", reaction="evade"), "주모"),
                         "캐묻기에 말을 돌리며 피했다")


class Parse(unittest.TestCase):
    def test_truncated_bad_json_and_extra_keys_fail_whole_game(self):
        for text, finish in (("{}", "length"), ("not json", "stop"), ('{"npcs": [], "x": 1}', "stop"),
                             ("x" * (rn.MAX_RESPONSE_BYTES + 1), "stop")):
            with self.assertRaises(rn.ParseFailed):
                rn.parse_response(text, finish)

    def test_parse_failure_gives_all_template(self):
        b = block([item("d1", "vote", "model_estimate", target="훈장")])
        result = rn.narrate([b], "{", "stop", LEX)
        self.assertTrue(result.parse_failed)
        self.assertEqual(result.npcs[0].source, rn.SOURCE_TEMPLATE)
        self.assertEqual(result.rejects, {rn.PARSE_FAILED: 1})


class Checks(unittest.TestCase):
    vote = item("d1", "vote", "model_estimate", target="훈장")

    def check(self, text, it=None, b=None):
        it = it or self.vote
        return rn.check_sentence(text, it, b or block([it]), LEX)

    def test_good_sentence_passes(self):
        self.assertIsNone(self.check("주모는 지금까지 나온 말과 표를 따져 훈장에게 표를 던졌습니다."))

    def test_format(self):
        self.assertEqual(self.check("주모는 훈장에게 표를 던졌다."), rn.FORMAT_LENGTH)
        self.assertEqual(self.check("주모는 훈장에게 표를 던졌습니다. 또 했습니다."), rn.FORMAT_LENGTH)
        self.assertEqual(self.check("주모는 " + "아주 " * 30 + "훈장에게 표를 던졌습니다."), rn.FORMAT_LENGTH)

    def test_listing_and_repetition_are_format_failures(self):
        self.assertEqual(self.check("주모는 훈장에게 표를 던졌다, 근거는 없음, 이유는 없음, 습니다."), rn.FORMAT_LENGTH)
        self.assertEqual(self.check("주모는 표를 표를 표를 훈장에게 던졌습니다."), rn.FORMAT_LENGTH)

    def test_past_plain_ending_is_normalized_before_checks(self):
        self.assertEqual(rn.normalize_ending("주모는 훈장에게 표를 던졌다."), "주모는 훈장에게 표를 던졌습니다.")
        self.assertEqual(rn.normalize_ending("주모는 착하다."), "주모는 착하다.")
        b = block([self.vote])
        text = reply([("jumo", [("d1", "주모는 지금까지 나온 말과 표를 따져 훈장에게 표를 던졌다.")])])
        result = rn.narrate([b], text, "stop", LEX)
        self.assertEqual(result.npcs[0].sentences, [("d1", "주모는 지금까지 나온 말과 표를 따져 훈장에게 표를 던졌습니다.")])

    def test_forbidden_entity(self):
        self.assertEqual(self.check("주모는 과부를 따라 훈장에게 표를 던졌습니다."), rn.FORBIDDEN_ENTITY)
        self.assertEqual(self.check("주모는 우물가에서 훈장에게 표를 던졌습니다."), rn.FORBIDDEN_ENTITY)
        ev = item("d1", "vote", "model_estimate", target="훈장", evidence=("훈장의 말: 1일째 밤 한밤에 우물가에 있었다",))
        self.assertIsNone(self.check("주모는 우물가에 있었다는 훈장의 말을 근거로 훈장에게 표를 던졌습니다.", ev))

    def test_action_mismatch(self):
        self.assertEqual(self.check("주모는 지금까지 나온 말과 표를 따져 훈장을 감쌌습니다."), rn.ACTION_MISMATCH)
        self.assertEqual(self.check("주모는 지금까지 나온 말과 표를 따져 표를 던졌습니다."), rn.ACTION_MISMATCH)
        self.assertEqual(self.check("주모는 훈장에게 표를 던지지 않았습니다."), rn.ACTION_MISMATCH)
        sus = item("d2", "suspect", "rule_suspect", target="훈장")
        self.assertEqual(self.check("주모는 훈장을 믿고 감쌌습니다.", sus), rn.ACTION_MISMATCH)

    def test_time_mismatch(self):
        self.assertEqual(self.check("주모는 3일째 훈장에게 표를 던졌습니다."), rn.TIME_MISMATCH)
        self.assertIsNone(self.check("주모는 1일째 투표에서 훈장에게 표를 던졌습니다."))

    def test_invented_cause(self):
        self.assertEqual(self.check("주모는 훈장이 도둑질을 해서 훈장에게 표를 던졌습니다."), rn.INVENTED_CAUSE)


class Narrate(unittest.TestCase):
    def test_failed_npc_falls_back_alone(self):
        a = block([item("d1", "vote", "model_estimate", target="훈장", template="주모는 훈장에게 표를 던졌습니다.")])
        b = block([item("d2", "vote", "model_estimate", actor="meosum", target="과부", template="머슴 템플릿.")],
                  name="머슴", npc="meosum")
        text = reply([("jumo", [("d1", "주모는 지금까지 나온 말과 표를 따져 훈장에게 표를 던졌습니다.")]),
                      ("meosum", [("d2", "머슴은 과부를 감쌌습니다.")])])
        result = rn.narrate([a, b], text, "stop", LEX)
        self.assertEqual([n.source for n in result.npcs], [rn.SOURCE_LLM, rn.SOURCE_TEMPLATE])
        self.assertEqual(result.npcs[1].sentences, [("d2", "머슴 템플릿.")])
        self.assertEqual(result.rejects, {rn.ACTION_MISMATCH: 1})

    def test_missing_or_duplicate_cite_is_cite_mismatch(self):
        a = block([item("d1", "vote", "model_estimate", target="훈장"),
                   item("d3", "suspect", "rule_suspect", target="과부", day=2)])
        text = reply([("jumo", [("d1", "주모는 훈장에게 표를 던졌습니다."), ("d1", "주모는 훈장에게 표를 던졌습니다.")])])
        self.assertEqual(rn.narrate([a], text, "stop", LEX).rejects, {rn.CITE_MISMATCH: 1})

    def test_merge_sums_rejects(self):
        r1 = rn.NarrationResult([], {rn.FORMAT_LENGTH: 1})
        r2 = rn.NarrationResult([], {rn.FORMAT_LENGTH: 2, rn.PARSE_FAILED: 1}, parse_failed=True)
        merged = rn.merge([r1, r2])
        self.assertEqual(merged.rejects, {rn.FORMAT_LENGTH: 3, rn.PARSE_FAILED: 1})
        self.assertTrue(merged.parse_failed)


class ViolationTable(unittest.TestCase):
    """위반 예시 20개 이상이 모두 거부된다(E-10-01 1번)."""
    vote = item("d1", "vote", "model_estimate", target="훈장")
    sus = item("d2", "suspect", "rule_suspect", target="과부", evidence=("과부의 말: 1일째 밤 한밤에 서낭당에 있었다",))
    night = item("d3", "night_plan", "night_accuser", actor="gwabu", victim="주모", phase="night")
    react = item("d4", "react", "react_evade", reaction="evade", phase="interrogation")
    CASES = [
        (vote, "주모는 훈장을 감쌌습니다."),
        (vote, "주모는 과부에게 표를 던졌습니다."),
        (vote, "주모는 지금까지 나온 말과 표를 따져 표를 던졌습니다."),
        (vote, "주모는 훈장에게 표를 던지지 않았습니다."),
        (vote, "주모는 2일째 훈장에게 표를 던졌습니다."),
        (vote, "주모는 주막에서 훈장에게 표를 던졌습니다."),
        (vote, "주모는 훈장이 돈을 훔쳐서 훈장에게 표를 던졌습니다."),
        (vote, "주모는 훈장에게 표를 던졌다, 근거는 없음, 이유는 없음."),
        (vote, "Jumo voted 훈장."),
        (vote, "주모는 훈장에게 표를 던졌습니다. 그리고 웃었습니다."),
        (sus, "주모는 과부를 믿고 감쌌습니다."),
        (sus, "주모는 훈장을 의심했습니다."),
        (sus, "주모는 우물가에 있었다는 과부의 말을 근거로 과부를 의심했습니다."),
        (sus, "주모는 3일째 과부를 의심했습니다."),
        (sus, "주모는 과부를 의심하지 않았습니다."),
        (sus, "주모는 과부가 머슴과 다투어서 과부를 의심했습니다."),
        (night, "과부는 그날 밤 훈장을 노렸습니다."),
        (night, "과부는 그날 밤 주모를 감쌌습니다."),
        (night, "과부는 우물가에서 주모를 노렸습니다."),
        (react, "주모는 캐묻기에 순순히 해명했습니다."),
        (react, "주모는 캐묻기에 비밀을 털어놓았습니다."),
        (react, "주모는 캐묻기를 피하지 않았습니다."),
    ]

    def test_all_rejected(self):
        self.assertGreaterEqual(len(self.CASES), 20)
        for it, text in self.CASES:
            name = "과부" if it.actor == "gwabu" else "주모"
            b = block([it], name=name, npc=it.actor)
            with self.subTest(text=text):
                self.assertIsNotNone(rn.check_sentence(rn.normalize_ending(text), it, b, LEX))


if __name__ == "__main__":
    unittest.main()


class SuspectBasis(unittest.TestCase):
    """11/11: 의심 근거 풀이(승인 문구)를 사실로 넘기고, 풀이를 반영한 문장은 검사를 통과한다."""
    sus = rn.NarrationItem("d5", 2, "round1", "hunjang", "suspect", "rule_suspect", target="머슴",
                           evidence=("머슴의 말: 1일째 밤 한밤에 우물가에 있었다",),
                           template="훈장은 근거를 들어 머슴을 의심했습니다.",
                           basis="머슴은 약초꾼이 사라진 그때 그 자리에 자기도 있었다고 말했습니다.")

    def test_prompt_has_basis_line_and_version(self):
        b = block([self.sus], name="훈장", npc="hunjang")
        text = json.dumps(rn.request_body([b], "마을 승리", 1)["messages"], ensure_ascii=False)
        self.assertIn("풀이: 머슴은 약초꾼이 사라진 그때 그 자리에 자기도 있었다고 말했습니다.", text)
        self.assertEqual(rn.PROMPT_VERSION, "review-v2")

    def test_sentence_using_basis_passes(self):
        b = block([self.sus], name="훈장", npc="hunjang")
        for text in ("훈장은 약초꾼이 사라진 그 자리에 있었다고 말한 머슴을 의심했습니다.",
                     "훈장은 약초꾼이 사라진 그때 우물가에 있었다는 머슴의 말을 근거로 머슴을 의심했습니다."):
            self.assertIsNone(rn.check_sentence(text, self.sus, b, LEX), text)

    def test_without_basis_victim_name_is_still_forbidden(self):
        it = rn.NarrationItem(**{**self.sus.__dict__, "basis": ""})
        b = block([it], name="훈장", npc="hunjang")
        self.assertEqual(rn.check_sentence("훈장은 약초꾼이 사라진 그 자리에 있었다고 말한 머슴을 의심했습니다.", it, b, LEX),
                         rn.FORBIDDEN_ENTITY)

    def test_template_fallback_keeps_basis_in_template(self):
        b = block([rn.NarrationItem(**{**self.sus.__dict__, "template": self.sus.template + " " + self.sus.basis})],
                  name="훈장", npc="hunjang")
        self.assertIn("사라진 그때", rn.template_summary(b).sentences[0][1])
