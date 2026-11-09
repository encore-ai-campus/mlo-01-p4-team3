"""결과·진척 화면에 공개할 문안과 수집한 단서만 고른다."""
import json
from django.conf import settings
from engine.domain.model import PRESETS, EndReason, Faction, Role
from engine.domain.progress import ending_available

# 선택형 도전 이름(B-08-01, E-09-08 프리셋의 `challenge` 값). 공개 여부는 presets_enabled.json이 정한다.
CHALLENGE_LABELS = {"divination_once": "점 결과를 한 번만 받음", "secret3": "비밀 있는 주민 3명",
                    "fewer_sightings": "목격이 줄어듦"}
ROLE_LABELS = {Role.POSSESSED: "씐 자", Role.VILLAGER: "마을 사람"}


def story_catalog(content_version=""):
    """고정 버전의 공개 문안을 읽는다. 새 배포도 이전 보관본을 유지한다."""
    if not content_version:
        content = json.loads((settings.BASE_DIR / "content/bundle_v1.json").read_text(encoding="utf-8"))
        content_version = content["content_version"]
    versions = json.loads((settings.BASE_DIR / "server/story_catalog.json").read_text(encoding="utf-8"))
    catalog = versions.get(content_version, {})
    return catalog.get("grudge_clues", []), catalog.get("endings", [])


def ending_options(progress):
    """결말의 제목과 해금 여부만 시작 폼에 전달한다. 결말 원문은 숨긴다."""
    _, endings = story_catalog(progress.content_version)
    if not endings:
        endings = [{"ending_id": "exorcism", "title_ko": "퇴마"},
                   {"ending_id": "haewon:h1", "title_ko": "한풀이"}]
    return [{"value": item["ending_id"], "title": item["title_ko"],
             "unlocked": ending_available(progress, item["ending_id"])} for item in endings]


def preset_choices(preset_ids):
    """공개 프리셋을 일반 판과 선택형 도전으로 나눈다. 도전 이름은 화면 문구, 값은 프리셋 ID 그대로다."""
    base, challenges = [], []
    for preset_id in preset_ids:
        preset = PRESETS[preset_id]
        if preset.challenge:
            label = CHALLENGE_LABELS.get(preset.challenge, preset.challenge)
            challenges.append({"value": preset_id, "label": f"{label} · {preset.n_active}인"})
        else:
            base.append(preset_id)
    return base, challenges


def review_context(output, content):
    """종료된 판의 복기 DTO를 화면 행으로 바꾼다. 정체·비밀은 판이 끝난 뒤에만 이 함수까지 온다.

    이유 코드가 있는 행동만 '행동의 이유'에 보인다. 행동 시점 이유(review_trace, E-10-10)가 연결되기 전에는
    비어 있어 그 영역을 숨긴다.
    """
    names = {c.npc_id: c.name_ko for c in content.characters}
    secret_titles = {s.secret_id: s.title_ko for s in content.secrets}
    clue_texts = {c.clue_id: c.text_ko for c in content.grudge_clues}
    residents = [{"name": names.get(npc_id, npc_id), "role": ROLE_LABELS.get(role, ""),
                  "possessed": role is Role.POSSESSED,
                  "secret": secret_titles.get(output.secrets.get(npc_id, ""), "")}
                 for npc_id, role in output.roles.items() if role is not Role.MUDANG]
    residents.sort(key=lambda row: (not row["possessed"], row["name"]))
    reasons = [{"actor": names.get(item.actor, item.actor), "reason_codes": list(item.reason_codes)}
               for item in output.timeline if item.reason_codes]
    return {"review": {"game_id": output.game_id, "residents": residents, "reasons": reasons,
                       "clues": [clue_texts.get(clue_id, "이전 콘텐츠 버전의 수집 단서")
                                 for clue_id in output.clues_gained]}}


def progress_context(progress):
    """본인이 얻은 단서의 문장·최초 획득 날짜와 결말 상태만 공개한다."""
    clues, _ = story_catalog(progress.content_version)
    texts = {item["clue_id"]: item["text_ko"] for item in clues}
    return {"collected_clues": [{"id": key, "text": texts.get(key, "이전 콘텐츠 버전의 수집 단서"),
                                  "day": record.first_day}
                                 for key, record in sorted(progress.clues.items())],
            "ending_options": ending_options(progress)}


def result_context(view, ending_choice, content_version):
    """공개 승패·종료 사유와 선택한 결말만 표시한다. 진실·비밀은 받지 않는다."""
    result = view.result
    if result is None:
        return {}
    reasons = {EndReason.ALL_POSSESSED_LOCKED: "씐 자를 모두 감금했습니다.",
               EndReason.MUDANG_LOCKED: "무당이 감금됐습니다.",
               EndReason.POSSESSED_PARITY: "씐 자가 남은 마을 사람과 무당 이상으로 늘었습니다."}
    _, endings = story_catalog(content_version)
    selected = next((item for item in endings if item["ending_id"] == ending_choice), None)
    lines = selected["lines_ko"] if selected and result.completed and result.winner is Faction.VILLAGE else []
    return {"game_result": {"winner": "마을 승리" if result.winner is Faction.VILLAGE else "창귀 승리",
                            "reason": reasons[result.reason], "day": result.day,
                            "completed": result.completed,
                            "ending_title": selected["title_ko"] if selected else "",
                            "lines": lines}}
