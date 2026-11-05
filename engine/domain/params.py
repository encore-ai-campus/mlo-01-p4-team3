"""밸런스 상수(규칙 파라미터). 시뮬레이션으로 조정하는 값만 둔다.

계층: 도메인. 표준 라이브러리만 사용한다.
값을 바꾸면 `RULE_PARAMS_VERSION`을 올린다. 판 메타(`GameMeta.rule_version`)에 이 값을 넣어 재현한다.
목격 확률·동선 무작위성은 피해 장소 공개 상태에서 승률 40~60%가 나오도록 A-08-03·A-10-01에서 조정한다.
"""
from __future__ import annotations

from .model import Place

RULE_PARAMS_VERSION = "rule-0.1"

# 같은 시간대·같은 공공장소에 있던 두 사람이 서로를 목격할 확률(쌍마다 한 번 뽑고 서로 본다).
# 초기 후보값이다. 조정 전까지 임시로 쓴다.
WITNESS_PROB = 0.7

# 배정 검증기 위반이 나오면 파생 시드로 다시 뽑는 최대 횟수(domain.md 10절). 넘으면 콘텐츠 부족으로 본다.
ASSIGNMENT_MAX_ATTEMPTS = 200

# 실행자의 0일째 한밤 거짓 행적(규칙 v0). 집은 공공장소가 아니라 목격으로 반박되지 않는다.
# 피해 장소가 피해자의 집(HOME)이면 집이라고 말할 수 없으므로 자기 동선의 공공장소 중에서 고른다.
EXECUTOR_ALIBI_PLACE = Place.HOME
