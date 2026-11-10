"""B-08-03 내부 광고 HTTP 어댑터. 브라우저 헤더·쿠키·리다이렉트를 전달하지 않는다."""
from dataclasses import asdict
import http.client
import json
import socket
from threading import Timer
import time
from urllib.parse import urlencode, urlsplit

from ads.domain.model import AdCreativeView, AdResponse
from server.app.ad_events import LANDING_PATH


class AdHTTPError(RuntimeError):
    """응답 형식·상태 오류. 메시지에는 주소·본문·토큰을 넣지 않는다."""


class HTTPAdProvider:
    """고정 내부 출처로 한 번만 요청한다. 실패 정규화와 회로 차단은 AdGateway가 맡는다."""

    def __init__(self, base_url):
        """설정 출처만 받는다. URL 인증·쿼리·하위 경로는 사용하지 않는다."""
        parts = urlsplit(base_url)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username
                or parts.password or parts.path not in ('', '/') or parts.query or parts.fragment):
            raise ValueError('invalid_ads_origin')
        self._scheme, self._host, self._port = parts.scheme, parts.hostname, parts.port

    def _request(self, method, path, request_id, connect_timeout_ms, total_timeout_ms, body=None):
        if not isinstance(request_id, str) or not request_id or any(ord(c) < 32 or ord(c) > 126 for c in request_id):
            raise ValueError('invalid_request_id')
        if not 0 < connect_timeout_ms <= total_timeout_ms:
            raise ValueError('invalid_timeouts')
        deadline = time.monotonic() + total_timeout_ms / 1000
        connection_type = http.client.HTTPSConnection if self._scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(self._host, self._port, timeout=connect_timeout_ms / 1000)
        timer = None
        headers = {'X-Request-ID': request_id}
        payload = None
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        try:
            connection.connect()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('ads_deadline')
            wire = connection.sock
            wire.settimeout(remaining)
            def expire():
                # shutdown interrupts slow/trickling headers and bodies, including Connection: close.
                try:
                    wire.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            timer = Timer(remaining, expire)
            timer.daemon = True
            timer.start()
            connection.putrequest(method, path, skip_accept_encoding=True)
            for name, value in headers.items():
                connection.putheader(name, value)
            if payload is not None:
                connection.putheader('Content-Length', str(len(payload)))
            connection.endheaders(payload)
            with connection.getresponse() as response:
                # One MiB is a local safety bound, not an extension of the shared DTO.
                data = response.read(1_048_577)
                if time.monotonic() >= deadline:
                    raise TimeoutError('ads_deadline')
                if len(data) > 1_048_576:
                    raise AdHTTPError('ads_response_too_large')
                if response.length not in (0, None):
                    raise AdHTTPError('ads_incomplete_response')
                return response.status, response.headers, data
        except TimeoutError:
            raise TimeoutError('ads_deadline') from None
        except (OSError, http.client.HTTPException):
            if time.monotonic() >= deadline:
                raise TimeoutError('ads_deadline') from None
            raise AdHTTPError('ads_transport_error') from None
        finally:
            if timer:
                timer.cancel()
            connection.close()

    def request_ad(self, req, connect_timeout_ms, total_timeout_ms):
        """AdRequest를 JSON으로 보내고 검증한 AdResponse DTO만 돌려준다."""
        status, headers, raw = self._request('POST', '/ads/v1/serve', req.request_id,
                                            connect_timeout_ms, total_timeout_ms, asdict(req))
        if status != 200 or headers.get_content_type() != 'application/json':
            raise AdHTTPError('ads_serve_status')
        try:
            data = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            allowed = set(AdResponse.__dataclass_fields__)
            if (not isinstance(data, dict) or set(data) - allowed or data.get('request_id') != req.request_id
                    or type(data.get('filled')) is not bool):
                raise ValueError()
            for key in ('serve_token', 'beacon_path', 'click_path'):
                if key in data and data[key] is not None and not isinstance(data[key], str):
                    raise ValueError()
            for key in ('policy_version', 'ctr_version'):
                if key in data and not isinstance(data[key], str):
                    raise ValueError()
            creative = data.get('creative')
            if creative is not None:
                if not isinstance(creative, dict) or any(not isinstance(v, str) for v in creative.values()):
                    raise ValueError()
                data['creative'] = AdCreativeView(**creative)
            if data['filled'] and (not data.get('creative') or not data.get('serve_token')):
                raise ValueError()
            return AdResponse(**data)
        except (ValueError, TypeError, UnicodeError):
            raise AdHTTPError('ads_serve_shape') from None

    def send_impression(self, serve_token, visible_ratio, visible_ms, occurred_at_ms,
                        request_id, connect_timeout_ms, total_timeout_ms):
        """계약의 네 비콘 필드만 전송하고 204만 성공으로 인정한다."""
        status, _, _ = self._request('POST', '/ads/v1/impression', request_id,
            connect_timeout_ms, total_timeout_ms, dict(token=serve_token, visible_ratio=visible_ratio,
                visible_ms=visible_ms, occurred_at_ms=occurred_at_ms))
        if status != 204:
            raise AdHTTPError('ads_impression_status')
        return True

    def send_click(self, serve_token, request_id, connect_timeout_ms, total_timeout_ms):
        """302를 따라가지 않고 같은 출처의 가상 랜딩 상대 경로만 반환한다."""
        status, headers, _ = self._request('GET', '/ads/v1/click?' + urlencode({'t': serve_token}),
                                          request_id, connect_timeout_ms, total_timeout_ms)
        target = headers.get('Location', '')
        if status != 302 or not LANDING_PATH.fullmatch(target):
            raise AdHTTPError('ads_click_status')
        return target

    def fetch_landing(self, creative_id, request_id, connect_timeout_ms, total_timeout_ms):
        """가상 소재 ID의 HTML만 읽는다. 실패 시 Gateway가 대체 표시로 전환한다."""
        path = '/ads/landing/' + creative_id
        if not LANDING_PATH.fullmatch(path):
            raise ValueError('invalid_creative_id')
        status, headers, raw = self._request('GET', path, request_id,
                                            connect_timeout_ms, total_timeout_ms)
        if status != 200 or headers.get_content_type() != 'text/html':
            raise AdHTTPError('ads_landing_status')
        return raw.decode('utf-8')
