"""결과·진척 화면에 공개할 문안과 수집한 단서만 고른다."""
import json
from django.conf import settings
from engine.domain.model import EndReason, Faction


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
             "unlocked": item["ending_id"] in progress.endings_unlocked} for item in endings]


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
