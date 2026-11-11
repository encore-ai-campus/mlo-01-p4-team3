"""인프라: E 노트북에서 계정마다 하나씩 도는 RunPod 실험 Pod 감시(E-08-13 6번, E-11-12 7번 ①②).

이름 접두어 `paldo-e1112-` Pod만 장부(JSON 파일)에 올려(삭제된 Pod도 남김) 시간당 요금 × 관측 시간 누계를 낸다.
- 누계가 계정 한도의 80 %(`DELETE_RATIO`)에 닿으면 살아 있는 접두어 Pod를 모두 삭제한다.
- Pod마다 첫 관측 시각 + 단계별 `MAX_MINUTES` + `GRACE_MINUTES`(10분)가 지나면 그 Pod를 삭제한다(Pod 안 자기 삭제와 독립).
- 접두어가 없는 Pod는 세지도 지우지도 않고 시작 때 경고만 한다.
- 계정 격리(11/8 사용자 결정으로 E가 네 계정을 한 노트북에서 다룬다): 이름의 계정 칸이 `--account`와 같은 Pod만 세고 지운다.
  다른 계정 칸이나 계정 칸을 못 읽는 접두어 Pod는 세지도 지우지도 않고 매 주기 오류로 알린다(키 격리 실패 또는 이름 규칙 위반 신호).
- `--key-env 이름`을 주면 그 환경 변수의 값을 `runpodctl` 자식 프로세스에만 `RUNPOD_API_KEY`로 넘기고,
  다른 계정 키 변수(`PALDO_RUNPOD_KEY_` 접두어)는 자식 환경에서 뺀다. 값은 읽어서 넘기기만 하고 출력하지 않는다.
- 시작 전에 콘솔 Billing 수동 확인(잔액, 자동 충전 꺼짐, 잔액 부족 알림) 질문에 `yes`를 입력해야 돈다.

    python -m llm.bench.watch_pods --account E --ledger out/runpod/ledger_E.json
    python -m llm.bench.watch_pods --account E --once                # 한 번만 보고 끝(점검용)
    python -m llm.bench.watch_pods --account B --key-env PALDO_RUNPOD_KEY_B --ledger out/runpod/ledger_B.json

Pod 이름 규칙: `paldo-e1112-<단계 s0|s1|s2>-<계정>-<번호>`(예: `paldo-e1112-s1-e-1`). 단계를 못 읽으면 가장 긴 상한을 쓰고 경고한다.

미확인(0단계 확인, 런북 `ops/runbook/gpu_demo.md`): `runpodctl` 목록 출력 형식. 기본 명령은 `runpodctl pod list -o json`이고
JSON 배열의 `id`·`name`·`costPerHr`를 읽는다. 다르면 `--list-cmd`와 `--fields`로 맞춘다. 디스크 요금은 세지 않는다(소프트 상한).
`--key-env`가 없으면 `runpodctl config` 설정 파일의 키를 쓴다. `runpodctl`이 `RUNPOD_API_KEY` 환경 변수를
설정 파일보다 먼저 쓰는지는 미확인이라 0단계에서 계정마다 `--once`로 그 계정 Pod만 보이는지 확인한다(런북 4절).

계층: 위쪽 순수 함수(장부 갱신·삭제 판단, 단독 테스트) / `RunpodCtl`(외부 명령 인프라, 가짜 명령으로 바꿔 테스트) / `watch`(응용 루프) / `main`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PREFIX = "paldo-e1112-"
LIMITS = {"E": 5.0, "B": 5.0, "C": 4.5, "D": 4.5}      # 11/8 사용자 결정(달러)
DELETE_RATIO = 0.8
MAX_MINUTES = {"s0": 20, "s1": 90, "s2": 90}           # run_pod.sh와 같은 값
GRACE_MINUTES = 10
INTERVAL_S = 60
DELETE_RETRIES = 3
KEY_ENV_PREFIX = "PALDO_RUNPOD_KEY_"                    # 계정별 키 변수 이름 접두어(값은 저장소 밖, 4th_private)
CONFIRM_TEXT = ("콘솔 Billing에서 ① 잔액(가능하면 한도만큼만 충전) ② 자동 충전 꺼짐 ③ 잔액 부족 알림(시작 잔액 − 한도)을 "
                "확인했으면 yes를 입력하세요: ")


# --- 순수 함수 ---
def stage_of(name: str) -> str | None:
    """Pod 이름에서 단계(s0|s1|s2). 규칙에 안 맞으면 None."""
    m = re.match(re.escape(PREFIX) + r"(s[012])-", name)
    return m.group(1) if m else None


def account_of(name: str) -> str | None:
    """Pod 이름에서 계정 글자(대문자 E|B|C|D). 단계 칸과 따로 읽는다(단계를 못 읽어도 계정은 읽음). 못 읽으면 None."""
    m = re.match(re.escape(PREFIX) + r"[^-]+-([a-zA-Z])-", name)
    return m.group(1).upper() if m and m.group(1).upper() in LIMITS else None


def max_minutes(name: str) -> int:
    """그 Pod의 최대 실행 시간(분). 단계를 못 읽으면 가장 긴 값."""
    return MAX_MINUTES.get(stage_of(name) or "", max(MAX_MINUTES.values()))


def update_ledger(ledger: dict, pods: list[dict], now: float, account: str | None = None) -> tuple[list[str], list[str]]:
    """목록 관측 한 번을 장부에 반영한다. pods는 {id, name, cost_per_hr}.

    돌려주는 값은 (접두어 없는 Pod 이름, 계정이 다르거나 못 읽는 접두어 Pod 이름). account를 주면 이름의 계정 칸이
    그 계정인 접두어 Pod만 장부에 올리고, 나머지 접두어 Pod는 두 번째 목록으로 돌려준다(세지도 지우지도 않음).

    접두어 Pod마다 첫·마지막 관측 시각과 시간당 요금을 남기고, 목록에서 사라진 Pod는 `gone`으로 표시한다(삭제 포함).
    비용 누계는 (마지막 관측 − 첫 관측) × 시간당 요금이다. 살아 있는 Pod는 판단할 때 한 간격을 더하고(`pod_cost`),
    사라진 Pod는 사라진 것을 본 시각까지 살아 있었다고 보고 마지막 관측 시각을 그 시각으로 올린다(보수적으로 더 센다).
    """
    seen, others, foreign = set(), [], []
    for p in pods:
        if not p["name"].startswith(PREFIX):
            others.append(p["name"])
            continue
        if account is not None and account_of(p["name"]) != account.upper():
            foreign.append(p["name"])
            continue
        e = ledger.setdefault(p["id"], {"name": p["name"], "first_seen": now, "last_seen": now,
                                        "cost_per_hr": p["cost_per_hr"], "gone": False, "delete_requested": False})
        e.update(last_seen=now, cost_per_hr=max(e["cost_per_hr"], p["cost_per_hr"]), gone=False)
        seen.add(p["id"])
    for pid, e in ledger.items():
        if pid not in seen and not e["gone"]:
            e["gone"], e["last_seen"] = True, now
    return others, foreign


def ctl_env(key_env: str | None, environ: dict[str, str]) -> dict[str, str] | None:
    """`runpodctl` 자식 프로세스 환경. key_env가 없으면 None(부모 환경 그대로). 변수가 없거나 비면 ValueError.

    다른 계정 키 변수(`PALDO_RUNPOD_KEY_` 접두어)는 빼고 그 계정 키만 `RUNPOD_API_KEY`로 넣는다.
    """
    if not key_env:
        return None
    key = environ.get(key_env, "")
    if not key:
        raise ValueError(f"환경 변수 {key_env}가 비어 있음")
    env = {k: v for k, v in environ.items() if not k.startswith(KEY_ENV_PREFIX) and k != "RUNPOD_API_KEY"}
    env["RUNPOD_API_KEY"] = key
    return env


def pod_cost(e: dict, interval_s: float = 0.0) -> float:
    """Pod 하나의 비용 추정(달러): (관측 시간 + 살아 있으면 한 간격) × 시간당 요금."""
    span = e["last_seen"] - e["first_seen"] + (0 if e["gone"] else interval_s)
    return span / 3600 * e["cost_per_hr"]


def total_cost(ledger: dict, interval_s: float = 0.0) -> float:
    """장부 전체 비용 추정."""
    return sum(pod_cost(e, interval_s) for e in ledger.values())


def decide_deletions(ledger: dict, now: float, limit: float, interval_s: float = 0.0) -> list[tuple[str, str]]:
    """삭제할 (Pod ID, 이유). 비용 누계가 한도 × 80 %에 닿으면 살아 있는 접두어 Pod 전부, 아니면 시간 초과 Pod."""
    alive = [(pid, e) for pid, e in sorted(ledger.items()) if not e["gone"]]
    if total_cost(ledger, interval_s) >= limit * DELETE_RATIO:
        return [(pid, "cost_80pct") for pid, _ in alive]
    return [(pid, "max_minutes") for pid, e in alive
            if now >= e["first_seen"] + (max_minutes(e["name"]) + GRACE_MINUTES) * 60]


def parse_pod_list(text: str, fields: tuple[str, str, str] = ("id", "name", "costPerHr")) -> list[dict]:
    """목록 명령의 JSON 출력 → [{id, name, cost_per_hr}]. 배열이 아니거나 필드가 없으면 ValueError."""
    data = json.loads(text or "[]")
    if isinstance(data, dict):
        data = data.get("pods", data.get("data", []))
    if not isinstance(data, list):
        raise ValueError("Pod 목록이 배열이 아님")
    fid, fname, fcost = fields
    try:
        return [{"id": str(p[fid]), "name": str(p[fname]), "cost_per_hr": float(p[fcost] or 0)} for p in data]
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Pod 목록 필드 없음: {exc}") from None


# --- 인프라 ---
class RunpodCtl:
    """`runpodctl` 호출. 명령 앞부분(`bin`)을 바꾸면 가짜 명령으로 테스트한다."""

    def __init__(self, bin_cmd: str = "runpodctl", list_cmd: str = "pod list -o json",
                 fields: tuple[str, str, str] = ("id", "name", "costPerHr"), env: dict[str, str] | None = None,
                 delete_cmd: str = "pod delete"):
        self.bin, self.list_args, self.fields, self.env = shlex.split(bin_cmd), shlex.split(list_cmd), fields, env
        self.delete_args = shlex.split(delete_cmd)

    def list_pods(self) -> list[dict]:
        """Pod 목록. 명령 실패면 RuntimeError."""
        r = subprocess.run([*self.bin, *self.list_args], capture_output=True, text=True, timeout=60, env=self.env)
        if r.returncode != 0:
            raise RuntimeError(f"목록 명령 실패(exit {r.returncode})")
        return parse_pod_list(r.stdout, self.fields)

    def delete_pod(self, pod_id: str, retries: int = DELETE_RETRIES, sleep: Callable[[float], None] = time.sleep) -> bool:
        """삭제 명령(기본 `pod delete`, 정지가 아니라 삭제) + `<id>`를 최대 retries번. 성공하면 True."""
        for i in range(retries):
            try:
                r = subprocess.run([*self.bin, *self.delete_args, pod_id], capture_output=True, text=True, timeout=60,
                                   env=self.env)
                if r.returncode == 0:
                    return True
            except (subprocess.TimeoutExpired, OSError):
                pass   # 멈추거나 못 띄우면 실패한 시도로 센다(감시는 계속)
            if i < retries - 1:
                sleep(5)
        return False


def log(msg: str, **extra) -> None:
    """구조화 로그 한 줄(표준 출력). Pod ID는 저장소에 남기지 않도록 증빙에 옮기지 않는다."""
    rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "service": "llm",
           "component": "watch_pods", "msg": msg, **extra}
    print(json.dumps(rec, ensure_ascii=False), flush=True)


# --- 응용 ---
def watch(ctl: RunpodCtl, ledger_path: Path, account: str, once: bool = False, interval_s: float = INTERVAL_S,
          clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
          max_loops: int | None = None) -> dict:
    """감시 루프. 장부를 매번 파일에 쓴다. 돌려주는 값은 마지막 장부."""
    limit = LIMITS[account]
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {}
    foreign_old = [e["name"] for e in ledger.values() if account_of(e["name"]) != account.upper()]
    if foreign_old:   # 다른 계정 장부를 잘못 넘김: 그 계정 기록을 지우거나 덮어쓰지 않도록 시작을 거부한다
        raise ValueError(f"장부 {ledger_path}에 계정 {account}가 아닌 항목이 있음: {foreign_old}")
    first, loops = True, 0
    while True:
        loops += 1
        now = clock()
        try:
            pods = ctl.list_pods()
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            log("목록 실패, 다음 주기에 다시", level="warning", error=str(exc))
            pods = None
        if pods is not None:
            others, foreign = update_ledger(ledger, pods, now, account)
            if first and others:
                log("접두어 없는 Pod가 있음(세지도 지우지도 않음)", level="warning", count=len(others))
            if foreign:
                log("계정 칸이 다르거나 못 읽는 실험 Pod가 보임(세지도 지우지도 않음): 키 격리·이름 규칙 확인, 콘솔에서 직접 처리",
                    level="error", account=account, names=foreign)
            first = False
            for pid, why in decide_deletions(ledger, now, limit, interval_s):
                ok = ctl.delete_pod(pid, sleep=sleep)
                ledger[pid]["delete_requested"] = True
                log("Pod 삭제" if ok else "Pod 삭제 실패: 콘솔에서 직접 삭제", level="info" if ok else "error",
                    reason=why, name=ledger[pid]["name"])
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            log("감시", account=account, cost_usd=round(total_cost(ledger, interval_s), 4),
                delete_line_usd=round(limit * DELETE_RATIO, 2), alive=sum(not e["gone"] for e in ledger.values()))
        if once or (max_loops and loops >= max_loops):
            return ledger
        sleep(interval_s)


def main(argv: list[str] | None = None, input_fn: Callable[[str], str] = input) -> int:
    """인자를 읽고 확인 질문을 거쳐 감시를 시작한다."""
    p = argparse.ArgumentParser(description="E-08-13 RunPod 실험 Pod 감시(비용 누계·시간 초과 삭제)")
    p.add_argument("--account", required=True, choices=sorted(LIMITS))
    p.add_argument("--ledger", default=None)
    p.add_argument("--once", action="store_true")
    p.add_argument("--interval", type=float, default=INTERVAL_S)
    p.add_argument("--runpodctl", default="runpodctl", help="명령 앞부분(테스트에서 가짜 명령)")
    p.add_argument("--list-cmd", default="pod list -o json")
    p.add_argument("--delete-cmd", default="pod delete",
                   help="삭제 하위 명령(설치된 runpodctl이 다르면 예: 'remove pod'). 뒤에 Pod ID가 붙는다")
    p.add_argument("--fields", nargs=3, default=("id", "name", "costPerHr"), metavar=("ID", "NAME", "COST"))
    p.add_argument("--key-env", default=None, help=f"그 계정 API 키가 든 환경 변수 이름(예: {KEY_ENV_PREFIX}B). 값은 출력하지 않음")
    a = p.parse_args(argv)
    try:
        env = ctl_env(a.key_env, dict(os.environ))
    except ValueError as exc:
        log(str(exc), level="error")
        return 2
    if input_fn(CONFIRM_TEXT).strip().lower() != "yes":
        log("확인 질문에 yes가 아니어서 시작하지 않음", level="error")
        return 2
    ledger = Path(a.ledger or f"out/runpod/ledger_{a.account}.json")
    try:
        watch(RunpodCtl(a.runpodctl, a.list_cmd, tuple(a.fields), env, a.delete_cmd), ledger, a.account, a.once, a.interval)
    except ValueError as exc:   # 장부 계정 불일치: 파일을 건드리지 않고 멈춤
        log(str(exc), level="error")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
