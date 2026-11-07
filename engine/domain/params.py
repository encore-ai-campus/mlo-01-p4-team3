"""밸런스 상수(규칙 파라미터). 시뮬레이션으로 조정하는 값만 둔다.

계층: 도메인. 표준 라이브러리만 사용한다.
값을 바꾸면 `RULE_PARAMS_VERSION`을 올린다. 판 메타(`GameMeta.rule_version`)에 이 값을 넣어 재현한다.

결정 층·관측 파라미터(동조 크기, 투표 잡음, q_max, 목격 확률, 기색 확률, 동선 무작위성)는 같은 폴더의
`decision_params.json`이 원본이다(A-08-03, 버전 `DECISION_PARAMS_VERSION`). 모듈을 불러올 때 한 번 읽어 아래
모듈 값으로 둔다. 엔진·NPC 규칙은 호출할 때마다 이 모듈 값을 읽으므로, 탐색 도구(`tools/optimize_decision.py`)는
`apply(DecisionParams)`로 한 프로세스 안의 값을 바꿔 판을 돌린다. 게임 웹은 `apply`를 부르지 않는다.
`RULE_PARAMS_VERSION`은 규칙 버전과 결정 파라미터 버전을 합친 문자열이라 판 메타만으로 값을 재현할 수 있다.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# 판 메타·회차 등록에 들어가는 버전 문자 집합(B 회차 검증 `server/app/test_sessions.py`와 같음). `+`는 쓰지 않는다.
VERSION_PATTERN = re.compile(r"[a-zA-Z0-9_.-]{1,80}")
VERSION_JOIN = "."   # 규칙 버전 + 결정 파라미터 버전 연결 문자(11/7: `+`가 회차 등록 검증에 걸려 `.`으로 바꿈)

RULE_BASE_VERSION = "rule-0.5"   # 0.2: 실행자 거짓 행적 공공장소 무작위. 0.3: TELL_PROB. 0.4: Q_MAX. 0.5: 결정 파라미터를 json으로 분리·동선 무작위성 추가(A-08-03)

DECISION_PARAMS_FILE = Path(__file__).with_name("decision_params.json")

# 배정 검증기 위반이 나오면 파생 시드로 다시 뽑는 최대 횟수(domain.md 10절). 넘으면 콘텐츠 부족으로 본다.
ASSIGNMENT_MAX_ATTEMPTS = 200


@dataclass(frozen=True)
class DecisionParams:
    """결정 층·관측 파라미터 한 벌(A-08-03 탐색 대상).

    - conformity_scale: 동조 항 크기. 성향 conformity 1.0·예상 득표 100%일 때 결정 점수에 더하는 확률 폭.
    - vote_noise: 결정 점수 잡음 폭(확률 단위 ±).
    - q_max: 무당 의심 결합 상한. q = q_max × (100 − trust_in_mudang)/100(ports.md 3).
    - witness_prob: 같은 시간대·같은 공공장소 두 사람이 서로 목격할 확률(쌍마다 한 번, domain.md 5절 3).
    - tell_prob: 거짓 사실 주장에 기색이 보일 확률(주장 ID당 한 번, domain.md 6-1).
    - routine_randomness: 동선 무작위성. 0이면 인물 동선 가중치 그대로, 1이면 같은 후보 장소를 균등하게 뽑는다.
    """
    version: str
    conformity_scale: float
    vote_noise: float
    q_max: float
    witness_prob: float
    tell_prob: float
    routine_randomness: float

    def __post_init__(self):
        for f in fields(self):
            if f.name == "version":
                if (not isinstance(self.version, str) or not VERSION_PATTERN.fullmatch(self.version)
                        or not VERSION_PATTERN.fullmatch(f"{RULE_BASE_VERSION}{VERSION_JOIN}{self.version}")):
                    raise ValueError(f"version은 영문·숫자·_ . -만 쓸 수 있음: {self.version!r}")
                continue
            v = getattr(self, f.name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0.0 <= v <= 1.0:
                raise ValueError(f"{f.name}는 0~1 실수여야 함: {v!r}")

    @classmethod
    def from_dict(cls, d: dict) -> "DecisionParams":
        """json 사전에서 만든다. 모르는 키(`note` 등)는 무시하고, 빠진 키는 오류."""
        return cls(**{f.name: d[f.name] for f in fields(cls)})

    def to_dict(self) -> dict:
        """json으로 쓸 사전."""
        return asdict(self)


def load(path: Path = DECISION_PARAMS_FILE) -> DecisionParams:
    """`decision_params.json`을 읽는다(모듈 불러오기 때 한 번, 패키지 안 고정 데이터)."""
    return DecisionParams.from_dict(json.loads(path.read_text(encoding="utf-8")))


DEFAULT = load()
_current = DEFAULT

DECISION_PARAMS_VERSION = DEFAULT.version
RULE_PARAMS_VERSION = f"{RULE_BASE_VERSION}{VERSION_JOIN}{DECISION_PARAMS_VERSION}"
CONFORMITY_SCALE = DEFAULT.conformity_scale
VOTE_NOISE = DEFAULT.vote_noise
Q_MAX = DEFAULT.q_max
WITNESS_PROB = DEFAULT.witness_prob
TELL_PROB = DEFAULT.tell_prob
ROUTINE_RANDOMNESS = DEFAULT.routine_randomness


def current() -> DecisionParams:
    """지금 프로세스에 적용된 결정 파라미터."""
    return _current


def apply(p: DecisionParams) -> None:
    """이 프로세스의 결정 파라미터를 p로 바꾼다(탐색 도구 전용). 버전 문자열도 함께 바꾼다."""
    global _current, DECISION_PARAMS_VERSION, RULE_PARAMS_VERSION, CONFORMITY_SCALE, VOTE_NOISE, Q_MAX
    global WITNESS_PROB, TELL_PROB, ROUTINE_RANDOMNESS
    _current = p
    DECISION_PARAMS_VERSION = p.version
    RULE_PARAMS_VERSION = f"{RULE_BASE_VERSION}{VERSION_JOIN}{p.version}"
    CONFORMITY_SCALE, VOTE_NOISE, Q_MAX = p.conformity_scale, p.vote_noise, p.q_max
    WITNESS_PROB, TELL_PROB, ROUTINE_RANDOMNESS = p.witness_prob, p.tell_prob, p.routine_randomness
