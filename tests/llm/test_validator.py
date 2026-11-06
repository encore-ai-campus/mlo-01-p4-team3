"""E-07-07 완료 조건 1: 검증기. 위반 예시 20개 이상이 모두 거부되고, 사유 4종이 각각 여러 개 들어간다."""
from __future__ import annotations

import json
import unittest
from collections import Counter
from pathlib import Path

from llm import validator as v

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = json.loads((ROOT / "fixtures" / "content_min" / "bundle.json").read_text(encoding="utf-8"))
LEX = v.lexicon_from_bundle(BUNDLE)
REQS = {r["job_id"]: r for r in json.loads((ROOT / "fixtures" / "llm" / "interrogation_requests.json")
                                         .read_text(encoding="utf-8"))}


def req(fact: str, names=("주모", "약초꾼", "대장장이", "머슴", "훈장"), fallback: str | None = None) -> dict:
    """RenderRequest 필드 dict(검증에 쓰는 필드만 의미 있음)."""
    return {"job_id": "t", "game_id": "g", "state_version": 1, "speech_id": "s", "npc_id": "jumo",
            "persona": "주모. 말투: 시원시원한 반말 섞인 하게체", "reaction": "evade", "fact_sentence": fact,
            "allowed_names": list(names), "fallback_text": fallback if fallback is not None else fact,
            "priority": 0, "deadline_ms": 15000}


WHERE = "그날 한밤엔 우물가에 있었소."
DAY = "2일째 밤 초저녁엔 주막에 있었소."
SEEN = "그날 한밤엔 서낭당에서 약초꾼을 봤소."
NOSEE = "1일째 밤 초저녁엔 우물가에서 아무도 못 봤소."
CONFESS = "…주막 뒷방에서 투전판에 끼었소. 식구들한테만은 말하지 마시오."

# (예시 ID, 사실 문장, 생성문, 기대 대표 사유)
VIOLATIONS = [
    # format_length
    ("F01", WHERE, "", v.FORMAT_LENGTH),
    ("F02", WHERE, "아이고 " + WHERE + " 정말 무서웠소" * 15, v.FORMAT_LENGTH),
    ("F03", WHERE, "주모: " + WHERE, v.FORMAT_LENGTH),
    ("F04", WHERE, "(한숨을 쉬며) " + WHERE, v.FORMAT_LENGTH),
    ("F05", WHERE, WHERE + "\n더 묻지 마시오.", v.FORMAT_LENGTH),
    ("F06", WHERE, "Well, " + WHERE, v.FORMAT_LENGTH),
    ("F07", WHERE, "그날 한밤엔 우물가에 있었소. 3경쯤이었소.", v.FORMAT_LENGTH),
    ("F08", WHERE, "에휴. 그래. 그날 한밤엔 우물가에 있었소. 됐소? 그만하오.", v.FORMAT_LENGTH),
    ("F10", WHERE, WHERE + " 그날 밤 우물가 근처에서 수상한 그림자를 본 것 같기도 하고 아닌 것 같기도 하오.", v.FORMAT_LENGTH),
    ("F09", WHERE, "그날 한밤엔 우물가에 있었소. 天地神明께 비오.", v.FORMAT_LENGTH),
    # forbidden_entity
    ("E01", WHERE, "그날 한밤엔 우물가에 있었소. 퇴기도 거기 있었소.", v.FORBIDDEN_ENTITY),
    ("E02", WHERE, "그날 한밤엔 우물가에 있었소. 주막엔 안 들렀소.", v.FORBIDDEN_ENTITY),
    ("E03", WHERE, "그날 한밤엔 우물가에 있었소. 창귀 짓은 아니오.", v.FORBIDDEN_ENTITY),
    ("E04", DAY, "2일째 밤 초저녁엔 주막에 있었소. 탁발승 스님하고 같이.", v.FORBIDDEN_ENTITY),
    ("E05", WHERE, "그날 한밤엔 우물가에 있었소. 관아에 물어보시오.", v.FORBIDDEN_ENTITY),
    ("E06", SEEN, "그날 한밤엔 서낭당에서 약초꾼을 봤소. 호랑이 울음도 들었소.", v.FORBIDDEN_ENTITY),
    ("E08", WHERE, WHERE + " 내가 사람을 죽였소.", v.FORBIDDEN_ENTITY),
    ("E07", WHERE, "그날 한밤엔 우물가에 있었소. 범인은 따로 있소.", v.FORBIDDEN_ENTITY),
    # added_assertion
    ("A01", WHERE, "그날 한밤엔 우물가에 있었소. 산에는 가지 않았소.", v.ADDED_ASSERTION),
    ("A02", WHERE, "틀림없이 그날 한밤엔 우물가에 있었소.", v.ADDED_ASSERTION),
    ("A03", WHERE, "그날 한밤엔 우물가에 있었소. 나는 무고하오.", v.ADDED_ASSERTION),
    ("A04", DAY, "맹세코 2일째 밤 초저녁엔 주막에 있었소.", v.ADDED_ASSERTION),
    ("A05", SEEN, "그날 한밤엔 서낭당에서 약초꾼을 봤소. 다른 사람은 없었소.", v.ADDED_ASSERTION),
    ("A06", WHERE, "그날 한밤엔 우물가에 있었소. 아무 짓도 안 했소.", v.ADDED_ASSERTION),
    ("A07", CONFESS, "…주막 뒷방에서 투전판에 끼었소. 식구들한테만은 말하지 마시오. 분명 한 판뿐이오.",
     v.ADDED_ASSERTION),
    # fact_changed
    ("C01", WHERE, "그날 한밤엔 서낭당에 있었소.", v.FACT_CHANGED),   # 장소 바뀜 → 금지 장소이기도 함(대표는 forbidden)
    ("C02", WHERE, "그날 밤엔 우물가에 있었소.", v.FACT_CHANGED),
    ("C03", DAY, "그날 초저녁엔 주막에 있었소.", v.FACT_CHANGED),
    ("C04", SEEN, "그날 한밤엔 서낭당에서 누굴 봤소.", v.FACT_CHANGED),
    ("C05", NOSEE, "1일째 밤 초저녁엔 우물가에서 누굴 봤소.", v.FACT_CHANGED),
    ("C06", WHERE, "그날 새벽엔 우물가에 있었소.", v.FACT_CHANGED),
    ("C07", WHERE, "에휴, 그냥 있었소.", v.FACT_CHANGED),
    ("C08", DAY, "3일째 밤 초저녁엔 주막에 있었소.", v.FACT_CHANGED),
    ("C09", "제사 전날인데 주막에서 술을 마셨네.", "제사 전날인데 주막에서 술을 팔았네.", v.FACT_CHANGED),   # 행동 바뀜
    ("C10", WHERE, "한밤 우물가.", v.FACT_CHANGED),   # 장소·시간만 남기고 나머지를 뺌
    ("C11", WHERE, "그날 한밤엔 우물가에 있었소?", v.FACT_CHANGED),   # 단언을 의문형으로 바꿈
    ("C12", WHERE, "히잉… 그날 한밤엔 우물가에 갔어요.", v.FACT_CHANGED),   # 서술어 어간이 바뀜
    ("C13", WHERE, "그날 한밤엔 우물가에 있지 않았어요.", v.ADDED_ASSERTION),   # 부정으로 바꿈(PM 11/6), fact_changed도 걸림
    ("C14", WHERE, "히잉… 그날 한밤엔 우물가에 있었을까요.", v.FACT_CHANGED),   # 의문(-ㄹ까요), 물음표 없음
    ("C15", WHERE, "그날 한밤엔 우물가에 있었다더라.", v.FACT_CHANGED),   # 전언(-더라)
    ("C16", WHERE, "그날 한밤엔 우물가에 있었다던데요.", v.FACT_CHANGED),   # 전언(-다던데)
    ("C17", WHERE, "그날 한밤엔 우물가에 있었나요.", v.FACT_CHANGED),   # 의문(-나요)
    ("C18", WHERE, "그날 한밤엔 우물가에 있었겠지요.", v.FACT_CHANGED),   # 추측(-겠-)
    ("C19", WHERE, "그날 한밤엔 우물가에 있었소 ?", v.FACT_CHANGED),   # 띄어 쓴 물음표(Codex 11/6)
    ("C20", WHERE, "그날 한밤엔 우물가에서 재미있었어요.", v.FACT_CHANGED),   # 어간이 다른 낱말 속에만 있음(Codex 11/6)
]
# C01은 장소를 바꿔 다른 장소를 말한 경우라 대표 사유가 forbidden_entity이고 fact_changed도 함께 걸린다.
PRIMARY_OVERRIDE = {"C01": v.FORBIDDEN_ENTITY, "C03": v.FACT_CHANGED, "C08": v.FORMAT_LENGTH}

PASSES = [
    ("P01", WHERE, WHERE),
    ("P02", WHERE, "그, 그게… 그날 한밤엔 우물가에 있었소. 더는 묻지 마시오."),
    ("P03", WHERE, "“그날 한밤엔 우물가에 있었소.”"),   # 전체를 감싼 따옴표는 normalize가 벗긴다
    ("P04", DAY, "허허, 2일째 밤 초저녁엔 주막에 있었소. 그게 다요."),
    ("P05", DAY, "이튿날 밤 초저녁엔 주막에 있었소. 왜 자꾸 묻는 게요?"),   # 2일째 = 이튿날
    ("P06", SEEN, "아이고, 그날 한밤엔 서낭당에서 약초꾼을 봤소."),   # '아이고'는 인물 '아이'가 아님
    ("P07", SEEN, "그날 한밤중엔 서낭당에서 박 영감을 봤소. 무당 양반도 아시잖소?"),   # 별칭·한밤중·무당
    ("P08", NOSEE, "1일째 밤 초저녁엔 우물가에서 아무도 못 봤소. 그러는 무당 양반은?"),
    ("P09", CONFESS, "하아… 주막 뒷방에서 투전판에 끼었소. 식구들한테만은 말하지 마시오."),
    ("P12", WHERE, "히잉… 그날 한밤엔 우물가에 있었어요. 그게 다예요."),   # 끝 서술어 어미를 아이 말투로(11/6)
    ("P13", CONFESS, "히잉… 주막 뒷방에서 투전판에 끼었어요. 식구들한테만은 말하지 마세요."),
    ("P14", DAY, "아따, 2일째 밤 초저녁엔 주막에 있었당께. 고것이 다여."),
    ("P15", WHERE, "그날 한밤엔 우물가에있었어요."),   # 끝 서술어 앞 띄어쓰기 빠짐(Codex 11/6)
    ("P11", WHERE, "그 날 한밤엔 우물가에 있었소. 그게 다요."),   # 띄어쓰기만 다름(11/5 측정에서 오판)
    ("P10", WHERE, "그날 한밤엔 우물가에 있었소. 어쩔 수 없었소."),   # '수 없' 은 부정 사실이 아님
]


def run(fact, text):
    return v.validate(v.normalize_output(text), req(fact), LEX)


class Violations(unittest.TestCase):
    def test_at_least_20_and_each_reason_several(self):
        self.assertGreaterEqual(len(VIOLATIONS), 20)
        primary = Counter(PRIMARY_OVERRIDE.get(cid, want) for cid, _, _, want in VIOLATIONS)
        for code in v.REASONS:
            self.assertGreaterEqual(primary[code], 3, code)

    def test_all_rejected_with_expected_reason(self):
        for cid, fact, text, want in VIOLATIONS:
            with self.subTest(cid):
                r = run(fact, text)
                self.assertFalse(r.ok, f"{cid} 통과하면 안 됨: {text}")
                self.assertIn(want, r.reasons, f"{cid}: {r.reasons} {r.details}")
                self.assertEqual(r.reason, PRIMARY_OVERRIDE.get(cid, want), f"{cid}: {r.reasons} {r.details}")
                self.assertTrue(set(r.reasons) <= set(v.REASONS))

    def test_passes(self):
        for cid, fact, text in PASSES:
            with self.subTest(cid):
                r = run(fact, text)
                self.assertTrue(r.ok, f"{cid}: {r.reasons} {r.details}")
                self.assertIsNone(r.reason)

    def test_contract_examples_fallbacks_pass(self):
        for r in REQS.values():
            res = v.validate(r["fallback_text"], r, LEX)
            self.assertTrue(res.ok, f"{r['job_id']}: {res.details}")


class Fallback(unittest.TestCase):
    def test_contains_fact(self):
        for r in REQS.values():
            self.assertIn(r["fact_sentence"], v.fallback_text(r))
            self.assertEqual(v.fallback_text(r), r["fallback_text"])

    def test_broken_fallback_uses_fact(self):
        self.assertEqual(v.fallback_text(req(WHERE, fallback="더 묻지 마시오.")), WHERE)
        self.assertEqual(v.fallback_text(req(WHERE, fallback="")), WHERE)


class Lexicon(unittest.TestCase):
    def test_mentions_longest_first(self):
        ms = v.find_mentions("대장간 최씨는 주막집에 있었소.", LEX)
        self.assertEqual([(m.kind, m.key) for m in ms], [("name", "대장장이"), ("place", "주막")])

    def test_word_boundary(self):
        self.assertEqual(v.find_mentions("아이고 우리집", LEX), [])   # 감탄사, 단어 중간의 '집'

    def test_fact_elements(self):
        e = v.fact_elements(SEEN, LEX)
        self.assertEqual((e.places, e.times, e.names), ({"서낭당"}, {"한밤"}, {"약초꾼"}))

    def test_json_roundtrip(self):
        self.assertEqual(v.Lexicon.from_json(json.loads(json.dumps(LEX.to_json()))), LEX)

    def test_canonicalize_and_missing_words(self):
        self.assertEqual(v.canonicalize("이튿날 밤 한밤중엔 박 영감을", LEX), "2일째 밤 한밤엔 약초꾼을")
        self.assertEqual(v.missing_fact_words("허허, " + DAY + " 그게 다요.", DAY, LEX), [])
        self.assertEqual(v.missing_fact_words("2일째 밤 초저녁엔 주막에 있었네.", DAY, LEX), [])   # 끝 서술어 어미는 말투로
        self.assertEqual(v.missing_fact_words("2일째 밤 초저녁엔 주막에 갔네.", DAY, LEX), ["있었소"])
        self.assertEqual(v.missing_fact_words("2일째 밤 초저녁엔 주막에 있었소?", DAY, LEX), ["있었소(의문형)"])

    def test_predicate_stem(self):
        self.assertEqual(v.predicate_stem("있었소"), "있었")
        self.assertEqual(v.predicate_stem("옮겼습니다요"), "옮겼")
        self.assertEqual(v.predicate_stem("마시오"), "마")
        self.assertEqual(v.predicate_stem("소"), "소")

    def test_allowed_places_override(self):
        text = "그날 한밤엔 우물가에 있었소. 주막엔 들르지 않았소."
        r = v.validate(text, req(WHERE), LEX, allowed_places=["우물가", "주막"])
        self.assertNotIn(v.FORBIDDEN_ENTITY, r.reasons)

    def test_normalize(self):
        self.assertEqual(v.normalize_output("<think>음</think>\n \"대사\" "), "대사")
        self.assertEqual(v.normalize_output("\"가\" 하고 \"나\""), "\"가\" 하고 \"나\"")   # 감싼 따옴표가 아님
        self.assertEqual(v.normalize_output(None), "")


if __name__ == "__main__":
    unittest.main()
