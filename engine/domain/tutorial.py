"""튜토리얼(따라 하기 판) 시나리오와 단계 규칙. 순수 함수만 둔다.

계층: 도메인. 표준 라이브러리와 같은 도메인 모듈만 import한다.
설계: 저장소 밖 `tutorial_design.md`(11/11 확정). 요지:
- 서버가 고정 시드·프리셋·규칙 버전으로 판을 만든다. 엔진은 같은 시드·콘텐츠·규칙 수치·명령 순서면 같은 판을 만든다.
- 현재 단계 = `state_version - START_VERSION`. 명령 하나가 받아들여질 때마다 버전이 1 오르므로 따로 저장할 값이 없다.
- 단계마다 허용 명령은 하나다(`STEPS`). 서버 응용 계층이 엔진 적용 전에 `matches`로 검사한다.
- 규칙 수치·NPC 정책·콘텐츠가 바뀌어 대본대로 가도 이기지 못하면 대본 끝까지 가는 시험이 먼저 깨진다.
  그때 `tools/tutorial_seed_search.py`로 시드를 다시 고르고 단계표와 대사를 고친다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import params
from .model import (Command, CommandKind, DivinationResult, EvidenceType, InterrogationKind, Phase, Slot)

SCENARIO_ID = "tutorial-v1"
SEED = 185
PRESET_ID = "base6"
START_VERSION = 0

# 규칙 버전은 서버 설정(`PALDO_RULE_BASE` 등 새 일반 판 규칙)을 따라가지 않고 이 상수로 고정한다.
# 투표 기권(rule-0.9)까지 가르친다(11/11 사용자 결정). 다른 규칙판으로 옮길 때는 이 상수를 바꾸고 대본 시험·시드 검색을 다시 돌린다.
RULE_BASE = "rule-0.9"

# 시드 185 대본을 확인한 콘텐츠 버전(설계 14절 결정 5). 운영 활성 버전이 이 안에 있을 때만 튜토리얼을 연다.
# base6은 추가 주민을 뽑지 않아 두 버전의 판이 같다(content.draw_pool).
CONTENT_VERSIONS = ("content-1.1", "content-1.0.1")

# 12단계 자유 입력 예시 문장. 배포 해석기에서 suspect(과부) 카드 1장이 되어야 한다(서버 시험이 확인).
UTTERANCE_TEXT = "과부가 영 수상하오"


def rule_version() -> str:
    """튜토리얼 판 메타의 규칙 버전(고정 규칙 + 배포 결정 파라미터 버전)."""
    return f"{RULE_BASE}{params.VERSION_JOIN}{params.DECISION_PARAMS_VERSION}"


@dataclass(frozen=True)
class Expected:
    """단계의 허용 명령. None 칸은 비교하지 않는 것이 아니라 '없어야 하는 값'이다(종류별 비교 칸만 본다)."""

    kind: CommandKind
    target: Optional[str] = None
    addressee: Optional[str] = None
    interrogation_kind: Optional[InterrogationKind] = None
    night: Optional[int] = None
    slot: Optional[Slot] = None
    evidence: frozenset = frozenset()          # 진술 ID 집합
    divination: Optional[tuple] = None         # (대상, 밤, 결과)
    via_utterance: bool = False                # 직접 입력창이 켜진 서버에서는 자유 입력 카드 확인으로만 받는다


@dataclass(frozen=True)
class Step:
    day: int
    phase: Phase
    expected: Expected


_CONTRA = frozenset({"cl04", "cl06"})

# 시드 185, rule-0.9, 모델 없음(11/11 실측, tools/tutorial_seed_search.py --abstain runoff1). 씐 자는 과부(gwabu).
# 1일째 결선에서 무당이 기권해 동률 → 무감금(무당이 과부를 찍으면 1일째에 끝나 점치기를 못 가르친다).
STEPS: tuple[Step, ...] = (
    Step(1, Phase.MORNING, Expected(CommandKind.ADVANCE)),
    Step(1, Phase.ROUND1, Expected(CommandKind.POINT_CONTRADICTION, evidence=_CONTRA)),
    Step(1, Phase.INTERROGATION, Expected(CommandKind.INTERROGATE, addressee="gwabu",
                                          interrogation_kind=InterrogationKind.WHEREABOUTS, night=0, slot=Slot.MIDNIGHT)),
    # 웹 양식은 모순 질문의 밤·시간 칸을 비활성으로 두어 보내지 않는다(game.js validateInterrogation).
    Step(1, Phase.INTERROGATION, Expected(CommandKind.INTERROGATE, addressee="hunjang",
                                          interrogation_kind=InterrogationKind.CONTRADICTION, evidence=_CONTRA)),
    Step(1, Phase.INTERROGATION, Expected(CommandKind.ADVANCE)),
    Step(1, Phase.ROUND2, Expected(CommandKind.DEFEND, target="hunjang")),
    Step(1, Phase.VOTE, Expected(CommandKind.VOTE, target="gwabu")),
    Step(1, Phase.RUNOFF, Expected(CommandKind.ABSTAIN)),
    Step(1, Phase.NIGHT, Expected(CommandKind.DIVINE, target="gwabu")),
    Step(2, Phase.MORNING, Expected(CommandKind.ADVANCE)),
    Step(2, Phase.ROUND1, Expected(CommandKind.REVEAL_DIVINATION, divination=("gwabu", 1, DivinationResult.MURKY))),
    Step(2, Phase.INTERROGATION, Expected(CommandKind.ADVANCE)),
    Step(2, Phase.ROUND2, Expected(CommandKind.SUSPECT, target="gwabu", via_utterance=True)),
    Step(2, Phase.VOTE, Expected(CommandKind.VOTE, target="gwabu")),
)


def is_tutorial(scenario_id: Optional[str]) -> bool:
    return scenario_id == SCENARIO_ID


def step_index(state_version: int) -> Optional[int]:
    """판 상태 버전 → 단계 번호. 단계표 밖이면 None."""
    index = state_version - START_VERSION
    return index if 0 <= index < len(STEPS) else None


def expected(state_version: int) -> Optional[Expected]:
    index = step_index(state_version)
    return STEPS[index].expected if index is not None else None


def _evidence_ids(cmd: Command) -> frozenset:
    """근거 진술 ID 집합. `claim:a` 여러 개와 `contradiction:a|b` 모두 같은 집합이 된다. 점괘 근거는 그대로 둔다."""
    ids = set()
    for ref in cmd.evidence:
        if ref.type is EvidenceType.CONTRADICTION:
            ids.update(ref.record_id.split("|"))
        else:
            ids.add(ref.record_id if ref.type is EvidenceType.CLAIM else f"{ref.type.value}:{ref.record_id}")
    return frozenset(ids)


def matches(exp: Expected, cmd: Command) -> bool:
    """명령이 단계 허용 명령과 같은가. 종류별로 의미 있는 칸만 비교한다(설계 6절 표)."""
    if cmd.kind is not exp.kind:
        return False
    k = cmd.kind
    if k in (CommandKind.ADVANCE, CommandKind.ABSTAIN):
        return True
    if k in (CommandKind.SUSPECT, CommandKind.DEFEND, CommandKind.VOTE, CommandKind.DIVINE):
        return cmd.target == exp.target
    if k is CommandKind.POINT_CONTRADICTION:
        return _evidence_ids(cmd) == exp.evidence
    if k is CommandKind.INTERROGATE:
        same = (cmd.addressee == exp.addressee and cmd.interrogation_kind is exp.interrogation_kind
                and cmd.night == exp.night and cmd.slot is exp.slot)
        if exp.interrogation_kind is InterrogationKind.CONTRADICTION:
            same = same and _evidence_ids(cmd) == exp.evidence
        return same
    if k is CommandKind.REVEAL_DIVINATION:
        d = cmd.divination
        return d is not None and (d.target, d.night, d.result) == exp.divination
    return False


def utterance_step(exp: Optional[Expected], free_text: bool) -> bool:
    """이 단계를 자유 입력 카드 확인으로 받는가. 직접 입력창을 끈 서버(`PALDO_FREE_TEXT_INPUT=off`)는 같은 명령을 버튼으로 받는다.
    두 경우 엔진 명령이 같아 판 흐름이 같다."""
    return exp is not None and exp.via_utterance and free_text


def allows(state_version: int, cmd: Command, via_utterance: bool, free_text: bool = True) -> bool:
    """이 상태 버전에서 이 명령을 받을 수 있는가. 자유 입력 단계(직접 입력창 켬)는 카드 확인으로만, 그 밖은 버튼으로만 받는다."""
    exp = expected(state_version)
    return exp is not None and utterance_step(exp, free_text) == via_utterance and matches(exp, cmd)


def on_script(state_version: int, day: int, phase: Phase, ended: bool, village_won: Optional[bool] = None) -> bool:
    """판이 대본대로 흘러가고 있는가(각본 이탈 검사, 1회차 시운전 반영).

    단계 번호는 상태 버전으로 정해지므로, 그 단계에 적어 둔 날짜·단계와 실제 판의 날짜·단계가 같아야 한다.
    끝난 판은 단계표 끝 버전에서 마을이 이겼을 때만 대본대로다. 어긋나면 족제비 안내가 거짓이 되므로
    화면은 안내를 멈추고 처음부터 다시 하기를 권한다.
    """
    if ended:
        return state_version - START_VERSION == len(STEPS) and bool(village_won)
    index = step_index(state_version)
    if index is None:
        return False
    step = STEPS[index]
    return step.day == day and step.phase is phase


def routes_to_tutorial(tutorial_completed: Optional[bool], session_id: Optional[str]) -> bool:
    """일반 판 '새로 시작'을 튜토리얼 판으로 보낼까(11/11 사용자 결정: 튜토리얼 강제).

    튜토리얼을 아직 시작하지 않은 계정의 회차 없는 일반 판 시작만 보낸다. 회차 판(고정 시드 테스트)은 회차 흐름을 지키려고
    가로채지 않는다. 진행 중인 판 이어 하기는 시작 요청이 아니므로 여기에 오지 않는다.
    """
    return session_id is None and not tutorial_completed


def first_game_guide(is_first_game: bool, tutorial_completed: Optional[bool]) -> bool:
    """첫 실제 판의 족제비 첫 판 안내를 켤까. 튜토리얼을 마친 계정은 기본 꺼짐(도움말 스위치로 다시 켜는 길은 그대로)."""
    return is_first_game and not tutorial_completed
