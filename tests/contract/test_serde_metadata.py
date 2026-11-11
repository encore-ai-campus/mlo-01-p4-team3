"""타입 정보 재사용이 매 입력의 엄격한 검증과 객체 격리를 보존하는지 확인한다."""
from __future__ import annotations

from dataclasses import dataclass, field
import typing
import unittest
from unittest.mock import patch

from engine.domain.serde import SchemaError, from_jsonable


@dataclass
class Record:
    count: int
    tags: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.count < 0:
            raise ValueError('negative_count')


@dataclass
class OtherRecord:
    count: str


class SerdeMetadata(unittest.TestCase):
    def test_repeated_records_resolve_type_information_once(self):
        @dataclass
        class Fresh:
            value: int
        with patch('typing.get_type_hints', wraps=typing.get_type_hints) as hints:
            self.assertEqual(from_jsonable(Fresh, {'value': 1}).value, 1)
            self.assertEqual(from_jsonable(Fresh, {'value': 2}).value, 2)
        self.assertEqual(sum(call.args[0] is Fresh for call in hints.call_args_list), 1)

    def test_warm_schema_still_rejects_each_invalid_input(self):
        from_jsonable(Record, {'count': 1})
        for data, message in [({'count': 1, 'extra': 0}, '없는 필드'),
                              ({}, '필수 필드 누락'), ({'count': True}, '정수여야'),
                              ({'count': -1}, 'negative_count'),
                              ({'count': 1, 'tags': [3]}, '문자열이어야')]:
            with self.subTest(data=data), self.assertRaisesRegex(SchemaError, message):
                from_jsonable(Record, data)

    def test_class_and_result_objects_are_separate(self):
        first = from_jsonable(Record, {'count': 1, 'tags': ['first']})
        first.tags.append('changed')
        self.assertEqual(from_jsonable(Record, {'count': 2, 'tags': ['second']}), Record(2, ['second']))
        self.assertEqual(from_jsonable(OtherRecord, {'count': 'text'}), OtherRecord('text'))
        with self.assertRaises(SchemaError):
            from_jsonable(OtherRecord, {'count': 1})
