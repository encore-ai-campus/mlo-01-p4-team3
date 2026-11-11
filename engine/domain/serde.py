"""dataclass ↔ JSON 변환. 표준 라이브러리만 사용한다.

`from_jsonable`은 엄격하다: 모르는 필드, 빠진 필수 필드, 잘못된 enum 값, 타입 불일치를
`SchemaError`로 거부한다. 픽스처·이벤트의 스키마 검사는 이 함수로 한다(계약의 단일 원천).
"""
from __future__ import annotations

import dataclasses
import enum
from functools import lru_cache
import json
import types
import typing
from typing import Any

_NONE = type(None)


class SchemaError(ValueError):
    """계약 스키마 위반. path는 위반 위치(예: `steps[2].command.kind`)."""

    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path


def to_jsonable(obj: Any) -> Any:
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {_key_to_str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    return obj


def _key_to_str(k: Any) -> str:
    if isinstance(k, enum.Enum):
        return str(k.value)
    return str(k)


def dumps(obj: Any, **kwargs: Any) -> str:
    return json.dumps(to_jsonable(obj), ensure_ascii=False, **kwargs)


def from_jsonable(tp: Any, data: Any, path: str = "$") -> Any:
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)

    if tp is Any:
        return data
    if origin in (typing.Union, types.UnionType):
        if data is None:
            if _NONE in args:
                return None
            raise SchemaError(path, "null 불가")
        non_none = [a for a in args if a is not _NONE]
        if len(non_none) != 1:
            raise SchemaError(path, f"지원하지 않는 Union: {tp}")
        return from_jsonable(non_none[0], data, path)
    if data is None:
        raise SchemaError(path, "null 불가")
    if origin in (list, tuple):
        if not isinstance(data, list):
            raise SchemaError(path, "배열이어야 함")
        item_tp = args[0] if args else Any
        items = [from_jsonable(item_tp, v, f"{path}[{i}]") for i, v in enumerate(data)]
        return tuple(items) if origin is tuple else items
    if origin is dict:
        if not isinstance(data, dict):
            raise SchemaError(path, "객체여야 함")
        kt, vt = args if args else (Any, Any)
        return {
            _key_from_str(kt, k, f"{path}.<key {k}>"): from_jsonable(vt, v, f"{path}.{k}")
            for k, v in data.items()
        }
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        try:
            return tp(data)
        except ValueError:
            allowed = [m.value for m in tp]
            raise SchemaError(path, f"{data!r}는 {tp.__name__} 값이 아님 (허용: {allowed})") from None
    if isinstance(tp, type) and dataclasses.is_dataclass(tp):
        return _dataclass_from(tp, data, path)
    if tp is bool:
        if not isinstance(data, bool):
            raise SchemaError(path, "bool이어야 함")
        return data
    if tp is int:
        if isinstance(data, bool) or not isinstance(data, int):
            raise SchemaError(path, "정수여야 함")
        return data
    if tp is float:
        if isinstance(data, bool) or not isinstance(data, (int, float)):
            raise SchemaError(path, "숫자여야 함")
        return float(data)
    if tp is str:
        if not isinstance(data, str):
            raise SchemaError(path, "문자열이어야 함")
        return data
    raise SchemaError(path, f"지원하지 않는 타입: {tp}")


def _key_from_str(kt: Any, key: str, path: str) -> Any:
    if kt is int:
        try:
            return int(key)
        except ValueError:
            raise SchemaError(path, "정수 키여야 함") from None
    if isinstance(kt, type) and issubclass(kt, enum.Enum):
        return from_jsonable(kt, key, path)
    return key


@lru_cache(maxsize=256)
def _dataclass_schema(tp: type):
    """정적인 계약 타입 정보만 재사용하고 JSON 값과 검증 결과는 보관하지 않는다."""
    return typing.get_type_hints(tp), {f.name: f for f in dataclasses.fields(tp)}


def _dataclass_from(tp: type, data: Any, path: str) -> Any:
    if not isinstance(data, dict):
        raise SchemaError(path, f"{tp.__name__} 객체여야 함")
    hints, fields = _dataclass_schema(tp)
    unknown = set(data) - set(fields)
    if unknown:
        raise SchemaError(path, f"{tp.__name__}에 없는 필드: {sorted(unknown)}")
    kwargs = {}
    for name, f in fields.items():
        if name in data:
            kwargs[name] = from_jsonable(hints[name], data[name], f"{path}.{name}")
        elif f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
            raise SchemaError(path, f"{tp.__name__} 필수 필드 누락: {name}")
    try:
        return tp(**kwargs)
    except ValueError as e:  # __post_init__ 검증
        raise SchemaError(path, str(e)) from None


def loads(tp: Any, text: str) -> Any:
    return from_jsonable(tp, json.loads(text))
