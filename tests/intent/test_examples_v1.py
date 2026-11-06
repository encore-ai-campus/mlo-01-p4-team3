"""E-06-02 의도 예시 데이터 검사: 규칙 함수 단독 테스트와 실제 파일 조건(개수·대조쌍·계보·마스킹·분할)."""
import unittest
from dataclasses import replace

from contracts.datasets import LabelSource
from intent.baseline import load_examples, split_examples
from server.app.interactions import mask_text
from tools import intent_examples as ie

SEED = "fixtures/intent/seed_examples.jsonl"
MIN_AUTHORED, MIN_CONTRAST, MIN_TYPE = 20, 5, 10   # P-14(11/6), 유형 하한은 11/6 PM 답


def variant(root, n, text, source=LabelSource.GENERATED):
    """원본 행에서 계보 규칙을 지킨 변형 행을 만든다(테스트용)."""
    key = replace(root.split_key, lineage_root=root.example_id)
    return replace(root, example_id=f"{root.example_id}_v{n}", text=text, label_source=source, split_key=key)


class RuleTests(unittest.TestCase):
    """규칙 함수를 작은 가짜 행으로 확인한다."""

    def setUp(self):
        rows = ie.load_rows()
        self.sus = next(r for r in rows if r.label == "suspect" and r.contrast_group)
        self.defend = next(r for r in rows if r.label == "defend" and r.contrast_group == self.sus.contrast_group)

    def test_note_info(self):
        self.assertEqual(ie.note_info("type=pronoun; ctx=selected_npc:<P1>"),
                         {"type": "pronoun", "ctx": "selected_npc:<P1>"})
        self.assertEqual(ie.note_info(""), {})

    def test_contrast_needs_two_labels(self):
        same = replace(self.defend, label="suspect")
        self.assertEqual(ie.contrast_counts([self.sus, same])["suspect"], 0)
        counts = ie.contrast_counts([self.sus, self.defend])
        self.assertEqual((counts["suspect"], counts["defend"]), (1, 1))

    def test_variants_not_counted_as_authored(self):
        v = variant(self.sus, 1, "그 <P1> 수상하오")
        self.assertEqual(ie.label_counts([self.sus, v])["suspect"], 1)
        self.assertEqual(ie.contrast_counts([self.sus, self.defend, v])["suspect"], 1)

    def test_lineage_rules(self):
        good = variant(self.sus, 1, "<P1>이 좀 수상하오")
        self.assertEqual(ie.lineage_errors([self.sus, good]), [])
        bad = [variant(self.sus, 1, self.sus.text),
               replace(good, label="defend"),
               replace(good, contrast_group=None),
               replace(good, example_id="other_v1")]
        for row in bad:
            self.assertTrue(ie.lineage_errors([self.sus, row]), row)
        many = [variant(self.sus, n, f"<P1> 수상 {'!' * n}") for n in range(1, 5)]
        self.assertTrue(any("variants" in e for e in ie.lineage_errors([self.sus] + many)))

    def test_slot_and_note_rules(self):
        self.assertTrue(ie.slot_errors([replace(self.sus, slots={})]))
        self.assertTrue(ie.slot_errors([replace(self.sus, slots={"target": "훈장"})]))
        self.assertTrue(ie.slot_errors([replace(self.sus, notes="type=pronoun")]))
        self.assertTrue(ie.slot_errors([replace(self.sus, notes="type=nickname")]))
        self.assertTrue(ie.slot_errors([replace(self.sus, notes="그냥 메모")]))
        self.assertTrue(ie.slot_errors([replace(self.sus, notes="register=old")]))
        quote = replace(self.sus, label="interrogate_suspicion_reason", form="quote",
                        slots={"addressee": "<P2>", "mentioned": "<P1>"}, notes="")
        self.assertTrue(ie.slot_errors([quote]))
        self.assertEqual(ie.slot_errors([replace(quote, notes="ctx=quoted_speaker:<P2>")]), [])
        self.assertTrue(ie.slot_errors([replace(quote, notes="ctx=quoted_speaker:<P1>")]))
        self.assertEqual(ie.slot_errors([replace(self.sus, notes="register=modern")]), [])
        self.assertEqual(ie.modern_counts([replace(self.sus, notes="register=modern")])["suspect"], 1)
        self.assertEqual(ie.slot_errors([replace(self.sus, notes="type=pronoun; ctx=last_addressee:<P1>")]), [])

    def test_result_metrics_flat(self):
        flat = ie.result_metrics({"rows": 3, "slot_errors": 0, "authored_by_label": {"suspect": 2}})
        self.assertEqual(flat, {"rows": {"value": 3, "unit": "개"}, "slot_errors": {"value": 0, "unit": "건"},
                                "authored_suspect": {"value": 2, "unit": "개"}})

    def test_duplicate_normalization(self):
        a = replace(self.sus, text="<P1>이 수상하오!")
        b = replace(self.defend, text="<P1>이  수상하오")
        self.assertEqual(ie.duplicate_texts([a, b]), [[a.example_id, b.example_id]])


class DataFileTests(unittest.TestCase):
    """`content/intent/examples_v1.jsonl` 완료 조건(E-06-02 1·2·3번)."""

    @classmethod
    def setUpClass(cls):
        cls.rows = load_examples(ie.DEFAULT_PATH)   # 엄격 로드 + example_id 중복 거부

    def test_authored_counts(self):
        counts = ie.label_counts(self.rows)
        self.assertEqual(set(counts), set(ie.LABELS))
        self.assertTrue(all(n >= MIN_AUTHORED for n in counts.values()), counts)

    def test_contrast_pairs(self):
        counts = ie.contrast_counts(self.rows)
        self.assertTrue(all(n >= MIN_CONTRAST for n in counts.values()), counts)

    def test_resolution_types(self):
        counts = ie.type_counts(self.rows)
        self.assertTrue(all(n >= MIN_TYPE for n in counts.values()), counts)

    def test_slots_and_lineage(self):
        self.assertEqual(ie.slot_errors(self.rows), [])
        self.assertEqual(ie.lineage_errors(self.rows), [])

    def test_modern_rows(self):
        counts = ie.modern_counts(self.rows)   # 11/6 사용자 결정: 의도마다 현대어 입력 2~3줄, 잡담 1~2줄
        self.assertTrue(all(1 <= n <= 3 for n in counts.values()), counts)
        self.assertTrue(all(n >= 2 for label, n in counts.items() if label != "unsupported"), counts)

    def test_ids_and_split(self):
        self.assertTrue(all(r.example_id.startswith("ex1_") for r in self.rows))
        self.assertTrue(all(r.split.value == "train" for r in self.rows))

    def test_no_duplicate_text_including_seed(self):
        self.assertEqual(ie.duplicate_texts(self.rows), [])
        self.assertEqual(ie.duplicate_texts(self.rows + load_examples(SEED)), [])

    def test_masking_changes_nothing(self):
        changed = [r.example_id for r in self.rows if mask_text(r.text) != r.text]
        self.assertEqual(changed, [])

    def test_split_keeps_labels_and_lineage(self):
        for rows in (self.rows, [r for r in self.rows if ie.is_authored(r)]):
            train, dev = split_examples(rows, seed=7)
            self.assertEqual({r.label for r in train}, set(ie.LABELS))
            self.assertEqual({r.label for r in dev}, set(ie.LABELS))
            self.assertFalse({r.split_key.lineage_root for r in train} & {r.split_key.lineage_root for r in dev})


if __name__ == "__main__":
    unittest.main()
