"""INT-07 웹 자동 재현 준비(인프라, `tools/perf/int07_web_run.py`가 하위 프로세스로 부른다).

계정 DB 이주, 동의한 참가자 1명과 로그인 세션, Mongo 회차 저장소에 고정 회차 등록(규칙 버전 `params.RULE_PARAMS_VERSION`).
결과(세션 쿠키·회차 ID·버전)를 JSON 파일로 쓴다. `tools/`는 제3자 라이브러리를 쓰지 않으므로 Django·pymongo 부분을 여기 둔다.

    DJANGO_SETTINGS_MODULE=server.tests.int07_run_settings python -m server.tests.int07_setup <결과 JSON> <시드>...
"""
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SESSION_PREFIX = "int07run"


def main(out_path: Path, seeds: list[int]) -> None:
    """준비를 하고 결과 JSON을 쓴다."""
    import django
    django.setup()
    from django.conf import settings
    from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
    from django.contrib.auth.models import User
    from django.contrib.sessions.backends.db import SessionStore
    from django.core.management import call_command
    from django.utils import timezone
    from pymongo import MongoClient
    from engine.domain import params
    from server.accounts.models import AccountProfile
    from server.app.test_sessions import TestSessionService
    from server.infra.test_sessions import MongoSessionStore
    from tools.check_content import load_bundle

    call_command("migrate", verbosity=0)
    user = User.objects.create_user(username=f"int07-{uuid.uuid4().hex[:8]}")
    profile = AccountProfile.objects.create(user=user, consented_at=timezone.now())
    session = SessionStore()
    session[SESSION_KEY] = str(user.pk)
    session[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    session[HASH_SESSION_KEY] = user.get_session_auth_hash()
    session.create()
    bundle = load_bundle(ROOT / "content/bundle_v1.json")
    client = MongoClient(os.environ["PALDO_MONGO_URI"], serverSelectionTimeoutMS=5000)
    try:
        sessions = TestSessionService(MongoSessionStore(client.get_default_database("paldo_web")))
        session_id = f"{SESSION_PREFIX}{uuid.uuid4().hex[:6]}"
        sessions.create(session_id, [{"preset_id": "base6", "seed": s} for s in seeds], [str(profile.player_pseudonym)],
                        {"rule_version": params.RULE_PARAMS_VERSION, "engine_version": "paldo-engine",
                         "content_version": bundle.content_version})
    finally:
        client.close()
    out_path.write_text(json.dumps({"cookie": [settings.SESSION_COOKIE_NAME, session.session_key],
                                    "session_id": session_id, "rule_version": params.RULE_PARAMS_VERSION,
                                    "content_version": bundle.content_version}), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), [int(s) for s in sys.argv[2:]])
