"""인프라: Kanana 생성 서버(llama-server, OpenAI 호환 `/v1/chat/completions`) HTTP 호출(E-10-01).

주소는 설정 값 `PALDO_REVIEW_LLM_URL`(예: `http://127.0.0.1:<port>`, 게임 서버에서 생성 기기로 가는 터널의 로컬 끝)이다.
표준 라이브러리 `urllib`만 쓴다. 응답 본문 외 원문 로그는 남기지 않는다.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from server.app.review_narration import NarratorError, NarratorReply, NarratorTimeout


class KananaNarrator:
    """`Narrator` 포트 구현."""

    def __init__(self, base_url: str):
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("invalid_review_llm_url")
        self.url = base_url.rstrip("/") + "/v1/chat/completions"

    def complete(self, body: dict, timeout: float) -> NarratorReply:
        """OpenAI 호환 /v1/chat/completions에 POST한다. 연결 실패는 NarratorError, 시간 초과는 NarratorTimeout."""
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(1 << 20)
        except (socket.timeout, TimeoutError):
            raise NarratorTimeout from None
        except urllib.error.HTTPError:
            raise NarratorError("http_error") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise NarratorTimeout from None
            raise NarratorError("unreachable") from None
        except (ConnectionError, OSError):
            raise NarratorError("unreachable") from None
        try:
            reply = json.loads(raw)
            choice = reply["choices"][0]
            text = choice["message"]["content"] or ""
            usage = reply.get("usage") or {}
            return NarratorReply(text, choice.get("finish_reason"), int(usage.get("prompt_tokens", 0)),
                                 int(usage.get("completion_tokens", 0)), str(reply.get("model", "")))
        except (ValueError, KeyError, IndexError, TypeError):
            return NarratorReply("", "invalid")     # 해석 불가 응답 → 도메인이 판 전체 템플릿으로 둔다
