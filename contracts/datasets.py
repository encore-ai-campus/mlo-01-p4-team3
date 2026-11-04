"""흐름 간 공유 데이터셋 계약(학습 행, 의도 예시, 계약 시나리오). 표준 라이브러리만 사용.

엔진 도메인이 아니라 데이터 계약이므로 engine/ 밖에 둔다. engine/은 이 모듈을 import하지 않는다.
상세: docs/contracts/events.md(학습 행), intent.md(의도 예시), parallel_dev.md(시나리오)
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional


class LabelSource(str, enum.Enum):
    HUMAN_ANNOTATION = "human_annotation"   # 신뢰 높음
    PLAYER_EDITED = "player_edited"         # 중간
    PLAYER_CONFIRMED = "player_confirmed"   # 낮음
    AUTO_EXECUTED = "auto_executed"         # 학습 제외 또는 약한 라벨
    AUTHORED = "authored"                   # 팀이 직접 작성한 시드
    GENERATED = "generated"                 # LLM 변형(검수 전)
    GENERATED_REVIEWED = "generated_reviewed"


class Split(str, enum.Enum):
    TRAIN = "train"
    DEV = "dev"
    TEST_HUMAN = "test_human"     # 고정 평가 세트. 학습 금지


@dataclass
class SplitKey:
    user: Optional[str]           # 가명 사용자 ID(사람 데이터)
    game_id: Optional[str]
    time_bucket: str              # 예: "2026-11-09"
    lineage_root: Optional[str] = None   # 생성 문장이면 원본 예시 ID(같은 분할로 묶음)


# ---------------------------------------------------------------- 씐 자 확률 모델 학습 행
@dataclass
class TrainingRow:
    row_id: str
    game_id: str
    snapshot_id: str
    observer: str
    candidate: str
    day: int
    phase: str
    preset_id: str
    n_active: int
    n_possessed: int
    n_possessed_remaining: int
    n_candidates: int
    feature_version: str
    features: dict[str, float]
    source: str                   # DataSource 값(human|bot|synthetic|scripted)
    split_key: SplitKey
    label: Optional[int] = None   # 판 종료 후 진실 조인으로만 채운다(1 = 씐 자)
    label_joined_at: Optional[str] = None


# ---------------------------------------------------------------- 의도 예시
@dataclass
class IntentExample:
    example_id: str
    text: str
    label: str                    # intent.md 라벨 ID
    form: str                     # assertion|question|quote|request
    slots: dict[str, Any]         # 예: {"target": "<P1>"} 인물은 자리표시자
    label_source: LabelSource
    split: Split
    split_key: SplitKey
    contrast_group: Optional[str] = None   # 최소 대조쌍 묶음
    notes: str = ""


# ---------------------------------------------------------------- 계약 시나리오
@dataclass
class ScenarioCheck:
    type: str                     # 체크 종류(parallel_dev.md 표)
    input: dict[str, Any]
    expect: dict[str, Any]
    note: str = ""


@dataclass
class Scenario:
    scenario_id: str
    description: str
    preset_id: str
    source: str
    setup: dict[str, Any]                         # 역할·비밀·행적 등 진실 배정(엔진 재생용)
    steps: list[dict[str, Any]] = field(default_factory=list)   # 명령 순서(엔진 재생용)
    checks: list[ScenarioCheck] = field(default_factory=list)
