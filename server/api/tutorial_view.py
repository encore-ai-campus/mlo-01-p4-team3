"""튜토리얼 판 화면 모델: 단계 정보 JSON·족제비 대사 줄·자유 입력 미리 채움(형식: 저장소 밖 `tutorial_step_format.md`).

계층: 표현(viewmodel). 단계 규칙은 도메인 `engine/domain/tutorial.py`, 대사는 `tutorial_script.py`에 있고,
여기서는 화면 선택자와 하위 단계(서버가 강제하지 않는 화면 조작 포함)만 정한다. 서버 강제는 응용 계층이 한다.
"""
from __future__ import annotations

import uuid

from engine.domain import tutorial
from engine.domain.model import CommandKind, Phase
from engine.ports import PlayerView
from server.app.tutorial_start import report_off_script
from .tutorial_script import (GUIDE_SPEAKER, TUTORIAL_CONFIRM, TUTORIAL_END, TUTORIAL_INTRO, TUTORIAL_OFF_SCRIPT,
                              TUTORIAL_STEPS, TUTORIAL_UTTERANCE_OFF, tutorial_display)
from .vn_script import VNLine

MASCOT = "mascot:jokjebi"


def _open(key, panel, button, say):
    return {"key": key, "action": "open", "target": f'[data-stage-open="{button}"]', "panel": panel, "say": say}


def _close(key, panel, say):
    return {"key": key, "action": "close", "target": f'dialog[data-panel-id="{panel}"] .stage-overlay-heading button',
            "panel": panel, "within": panel, "say": say}


def _submit(target, say, key="submit"):
    return {"key": key, "action": "submit", "target": target, "say": say}


def _form(kind, target=None):
    return f'form[data-command-kind="{kind}"]' + (f'[data-command-target="{target}"]' if target else "")


def _sheet(kind, say):
    return {"key": "open_sheet", "action": "toggle", "target": f'details[data-action-sheet="{kind}"]', "say": say}


def _select(form, field, value, say):
    return {"key": f"select_{field}", "action": "select", "target": f"{form} [name={field}]", "value": value, "say": say}


ADVANCE = _form("advance") + " button[type=submit]"
# 각본 이탈 때 강조할 머리 막대의 처음부터 다시 버튼(meeting_parts/tutorial_bar.html).
RESTART_SUB = {"key": "restart", "action": "click", "target": "[data-tutorial-restart] button[type=submit]"}

# 서버가 강제하지 않는 화면 조작을 앞에 붙일 단계(단계 번호 → 종류). 단계 번호는 engine/domain/tutorial.STEPS 순서.
UI_EXTRA = {1: "compare_records", 4: "notebook", 9: "alibi_grid"}


def _evidence(exp) -> list[str]:
    return [f"claim:{cid}" for cid in sorted(exp.evidence)]


def _command_substeps(exp, say, free_text: bool) -> list[dict]:
    """허용 명령을 내는 화면 조작(마지막은 제출). 대상·값은 도메인 단계표에서 읽는다. `say(key)`는 말풍선 문장."""
    k = exp.kind
    if k is CommandKind.ADVANCE:
        return [_submit(ADVANCE, say("submit"))]
    if k is CommandKind.ABSTAIN:
        return [_submit(_form("abstain") + " button[type=submit]", say("submit"))]
    if k is CommandKind.VOTE:
        return [_submit(_form(k.value, exp.target) + " button[type=submit]", say("submit"))]
    if k is CommandKind.DIVINE:
        # 밤 점치기는 사람을 먼저 고르고(누름 표시) 아래 [점치기] 버튼으로 낸다(ui-vn.js, meeting_actions.html).
        return [{"key": "pick", "action": "mark", "target": _form(k.value, exp.target) + " button[type=submit]",
                 "say": say("pick")},
                _submit("[data-divination-submit]", say("submit"))]
    if k is CommandKind.SUSPECT and tutorial.utterance_step(exp, free_text):
        return [_submit("form[data-utterance-form] button[type=submit]", say("send_text"), "send_text"),
                _submit("#speech-cards form[data-card-confirm] button[type=submit]", say("confirm_card"), "confirm_card")]
    f = _form(k.value)
    if k in (CommandKind.SUSPECT, CommandKind.DEFEND):
        # 직접 입력창을 끈 서버의 자유 입력 단계도 같은 의심 명령을 버튼으로 낸다.
        return [_sheet(k.value, say("open_sheet")), _select(f, "target", exp.target, say("select_target")),
                _submit(f"{f} button[type=submit]", say("submit"))]
    evidence = [{"key": "open_evidence", "action": "toggle", "target": f"{f} details.evidence-picker",
                 "say": say("open_evidence")},
                {"key": "check_evidence", "action": "check", "target": f"{f} input[name=evidence]",
                 "values": _evidence(exp), "say": say("check_evidence")}]
    if k is CommandKind.POINT_CONTRADICTION:
        return [_sheet(k.value, say("open_sheet")), *evidence, _submit(f"{f} button[type=submit]", say("submit"))]
    if k is CommandKind.INTERROGATE:
        out = [_sheet(k.value, say("open_sheet")), _select(f, "addressee", exp.addressee, say("select_addressee")),
               _select(f, "interrogation_kind", exp.interrogation_kind.value, say("select_interrogation_kind"))]
        if exp.evidence:
            # 모순 질문의 밤·시간 칸은 game.js가 비활성으로 만들어 보내지 않는다(서버 비교 값도 없음).
            out += evidence
        else:
            out += [_select(f, "night", str(exp.night), say("select_night")),
                    _select(f, "slot", exp.slot.value, say("select_slot"))]
        return out + [_submit(f"{f} button[type=submit]", say("submit"))]
    if k is CommandKind.REVEAL_DIVINATION:
        target, night, result = exp.divination
        return [_sheet(k.value, say("open_sheet")),
                _select(f, "divination", f"{night}:{target}:{result.value}", say("select_divination")),
                _submit(f"{f} button[type=submit]", say("submit"))]
    raise ValueError(f"튜토리얼 화면 단계가 없는 명령: {k.value}")


def _sayer(script: dict):
    """하위 단계 키 → 화면 말풍선 문장. 대본에 없는 키는 그 단계 마지막 말풍선 문장을 쓴다."""
    says = script.get("say", {})
    fallback = list(says.values())[-1] if says else script["lines"][-1]
    return lambda key: tutorial_display(says.get(key, fallback))


def _substeps(step: int, script: dict, free_text: bool = True) -> list[dict]:
    """단계 번호 → 하위 단계. 각 하위 단계의 `say`는 그 조작 차례에만 보이는 말풍선 문장이다."""
    say = _sayer(script)
    exp = tutorial.STEPS[step].expected
    extra = UI_EXTRA.get(step)
    pre = []
    if extra == "compare_records":
        pre = [_open("open_records", "meeting-records", "meeting-records", say("open_records")),
               {"key": "pick_claims", "action": "check", "target": "#meeting-records [data-record-select]",
                "values": _evidence(exp), "within": "meeting-records", "say": say("pick_claims")},
               {"key": "compare", "action": "click", "target": "#meeting-records [data-record-compare]",
                "within": "meeting-records", "say": say("compare")},
               _close("close_records", "meeting-records", say("close_records"))]
    elif extra == "notebook":
        suspect = next(s.expected.target for s in tutorial.STEPS if s.expected.kind is CommandKind.DIVINE)
        pre = [_open("open_notebook", "stage-notebook", "stage-notebook", say("open_notebook")),
               {"key": "mark_suspect", "action": "mark",
                "target": f'#stage-notebook [data-mark-person="{suspect}"][data-mark-value="100"]',
                "within": "stage-notebook", "say": say("mark_suspect")},
               _close("close_notebook", "stage-notebook", say("close_notebook"))]
    elif extra == "alibi_grid":
        pre = [_open("open_grid", "meeting-alibi-grid", "meeting-alibi-grid", say("open_grid")),
               _close("close_grid", "meeting-alibi-grid", say("close_grid"))]
    return pre + _command_substeps(exp, say, free_text)


def step_script(step: int, free_text: bool = True) -> dict:
    """단계 대본({lines, say}). 자유 입력 단계는 직접 입력창 설정에 따라 다른 대본을 쓴다."""
    exp = tutorial.STEPS[step].expected
    if exp.via_utterance and not free_text:
        return TUTORIAL_UTTERANCE_OFF
    return TUTORIAL_STEPS[step]


def step_lines(step: int, free_text: bool = True) -> tuple:
    """단계 대사창 줄(원문)."""
    return step_script(step, free_text)["lines"]


def _lines(step, view: PlayerView, free_text: bool):
    """(앞에 둘 줄, 뒤에 둘 줄). 각 항목은 (줄 ID, 원문). 판 시작 인사는 아침 진술보다 앞에 둔다(1회차 시운전)."""
    if view.result is not None:
        return [], [(f"tutorial-end-{i}", text) for i, text in enumerate(TUTORIAL_END)]
    lead = [(f"tutorial-intro-{i}", text) for i, text in enumerate(TUTORIAL_INTRO)] if step == 0 else []
    suffix = "b" if tutorial.STEPS[step].expected.via_utterance and not free_text else ""
    return lead, [(f"tutorial-{step}{suffix}-{i}", text) for i, text in enumerate(step_lines(step, free_text))]


def _after(step, view: PlayerView, free_text: bool) -> dict:
    """족제비 줄 ID → 바로 앞에 와야 할 대사창 줄 ID(대본 `after`). 화면(ui-vn.js)이 그 줄 뒤로 옮긴다."""
    if step is None or view.result is not None:
        return {}
    suffix = "b" if tutorial.STEPS[step].expected.via_utterance and not free_text else ""
    return {f"tutorial-{step}{suffix}-{i}": f"{prefix}-{view.day}"
            for i, prefix in step_script(step, free_text).get("after", {}).items()}


def tutorial_context(view: PlayerView, scenario_id, mudang_variant, free_text: bool = True) -> dict:
    """회의 화면에 더할 튜토리얼 키(형식 문서 1절). 튜토리얼 판이 아니면 `tutorial_mode` 거짓만 돌려준다.

    판 흐름이 대본과 어긋나면(`tutorial.on_script` 거짓) 단계 안내를 멈추고 다시 하기를 권하는 한 줄만 보이며 로그를 남긴다.
    """
    if not tutorial.is_tutorial(scenario_id):
        return {"tutorial_mode": False, "tutorial": None, "tutorial_lines": [], "tutorial_lead_lines": [], "tutorial_after": {},
                "utterance_prefill": ""}
    ended = view.result is not None
    won = (view.result.winner is not None and view.result.winner.value == "village") if ended else None
    on_script = tutorial.on_script(view.state_version, view.day, view.phase, ended, won)
    step = None if ended or not on_script else tutorial.step_index(view.state_version)
    if on_script:
        lead, rest = _lines(step, view, free_text)
    else:
        report_off_script(view.game_id, view.state_version, view.day, view.phase.value)
        lead, rest = [], [(f"tutorial-off-{view.state_version}", TUTORIAL_OFF_SCRIPT)]
    phase = view.phase.value
    sprite = "" if view.phase is Phase.NIGHT else MASCOT

    def line(line_id, text):
        return VNLine(line_id, view.day, phase, GUIDE_SPEAKER, tutorial_display(text), sprite_id=sprite, guide=True)
    lead_vn, rest_vn = [line(*x) for x in lead], [line(*x) for x in rest]
    data = {"scenario": tutorial.SCENARIO_ID, "step": step, "total": len(tutorial.STEPS), "ended": ended,
            "won": won, "off_script": not on_script,
            "lines": [{"line_id": x.line_id, "text": x.text} for x in lead_vn + rest_vn],
            "substeps": (_substeps(step, step_script(step, free_text), free_text) if step is not None
                         else [] if on_script else [RESTART_SUB | {"say": tutorial_display(TUTORIAL_OFF_SCRIPT)}]),
            "confirm_say": tutorial_display(TUTORIAL_CONFIRM),
            "exit_url": "/",
            "restart": {"url": "/tutorial", "start_token": str(uuid.uuid4()), "mudang_variant": mudang_variant or "f"}}
    exp = None if step is None else tutorial.STEPS[step].expected
    return {"tutorial_mode": True, "tutorial": data, "tutorial_lines": rest_vn, "tutorial_lead_lines": lead_vn,
            "tutorial_after": _after(step, view, free_text) if on_script else {},
            "utterance_prefill": tutorial.UTTERANCE_TEXT if tutorial.utterance_step(exp, free_text) else ""}
