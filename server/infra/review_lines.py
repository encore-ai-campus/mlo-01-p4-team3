"""복기 템플릿 해설 문구 파일(`content/review_lines_v1.json`, E-10-10)을 읽는다.

계층: 인프라(파일 읽기). 응용 `GameService.review_lines`에 {phrase_id: [변형]} 사전으로 넣는다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "content" / "review_lines_v1.json"


def load_review_lines(path: Optional[Path] = None) -> dict[str, list[str]]:
    """문구 파일을 읽어 문구 ID → 변형 목록으로 돌려준다. 문자열이 아닌 변형은 버린다."""
    document = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    return {str(key): [v for v in values if isinstance(v, str) and v]
            for key, values in document.get("lines", {}).items()}
