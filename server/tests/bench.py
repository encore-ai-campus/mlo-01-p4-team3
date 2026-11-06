"""Mongo 저장소 인수 전의 B-06-02 메모리 부하 사전 시험."""
import argparse
from contextlib import ExitStack
import json
import logging
import math
import os
import secrets
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Barrier


def main():
    """사용자별 독립 판에서 녹화 명령을 반복하고 p50·p95·오류 수를 출력한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--minutes", type=float, default=3)
    parser.add_argument("--memory", action="store_true", required=True, help="시험용 메모리 저장소를 사용합니다.")
    args = parser.parse_args()
    if not 1 <= args.users <= 100 or not 0 < args.minutes <= 30:
        parser.error("users는 1~100, minutes는 0 초과 30 이하입니다.")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ["DJANGO_SETTINGS_MODULE"] = "server.paldo_web.test_settings"
    os.environ["DJANGO_SECRET_KEY"] = secrets.token_urlsafe(48)
    os.environ["PALDO_PUBLIC_ORIGIN"] = "http://testserver"
    import django
    from django.conf import settings
    from django.core.management import call_command
    from django.db import connections
    from server.paldo_web.composition import configure_game_service
    with NamedTemporaryFile(prefix="paldo-bench-", suffix=".sqlite3", delete_on_close=False) as database:
        database.close()
        settings.DATABASES["default"]["NAME"] = database.name
        django.setup()
        from django.contrib.auth.models import User
        from django.test import Client
        from server.tests.support import issue_test_csrf, make_service, temporary_test_profiles
        from server.tests.test_concurrent_commands import AtomicRepositoryDouble

        with temporary_test_profiles() as register, ExitStack() as cleanup:
            cleanup.callback(connections.close_all)
            cleanup.callback(configure_game_service, None)
            service = make_service(repository=AtomicRepositoryDouble())
            service.load_test = True
            call_command("migrate", verbosity=0, interactive=False)
            configure_game_service(service)
            logging.getLogger("paldo.web").disabled = True
            clients = []
            for index in range(args.users):
                user = User.objects.create_user(username=f"benchmark-{index}")
                register(user)
                client = Client(enforce_csrf_checks=True)
                client.force_login(user)
                clients.append((client, issue_test_csrf(client)))
            barrier = Barrier(args.users)
            steps = service.engine.steps[1:]

            def run(session):
                """각 사용자는 초당 1요청을 보내며 판 종료 뒤 새 판을 시작한다."""
                client, csrf_token = session
                latencies, errors = [], 0
                barrier.wait()
                deadline, gid, step = time.monotonic() + args.minutes * 60, None, 0
                try:
                    while time.monotonic() < deadline:
                        started = time.monotonic()
                        if gid is None:
                            path, body = "/games", {"preset_id": "base6", "session_id": "test-session", "mudang_variant": "f"}
                        else:
                            path, body = f"/games/{gid}/commands", dict(steps[step]["request"], game_id=gid, command_id=str(uuid.uuid4()))
                        response = client.post(path, json.dumps(body), content_type="application/json", HTTP_ACCEPT="application/json",
                                               HTTP_X_CSRFTOKEN=csrf_token)
                        latencies.append((time.monotonic() - started) * 1000)
                        if response.status_code != 200:
                            errors += 1
                            gid, step = None, 0
                        elif gid is None:
                            gid = response.json()["view"]["game_id"]
                        else:
                            step += 1
                            if step == len(steps):
                                gid, step = None, 0
                        time.sleep(max(0, min(1 - (time.monotonic() - started), deadline - time.monotonic())))
                finally:
                    connections.close_all()
                return latencies, errors

            with ThreadPoolExecutor(max_workers=args.users) as pool:
                results = list(pool.map(run, clients))
            values = sorted(value for samples, _ in results for value in samples)
            errors = sum(count for _, count in results)
            report = {"storage": "memory-preflight", "users": args.users,
                      "minutes": args.minutes, "requests": len(values), "errors": errors,
                      "p50_ms": round(values[math.ceil(len(values) * .50) - 1], 3),
                      "p95_ms": round(values[math.ceil(len(values) * .95) - 1], 3)}
            print(json.dumps(report), flush=True)
            return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
