"""htmx 폼을 엄격한 공통 DTO 입력으로 변환한다."""
from .views import _meeting_command_body


def utterance_form(form, resolving=False):
    """화면 폼의 문자열·정수·근거를 JSON 계약과 같은 필드로 만든다."""
    common = {"csrfmiddlewaretoken", "game_id", "state_version"}
    allowed = common | ({"attempt_id", "reaction", "command_id", "chosen_index", "retry_text", "kind",
                         "target", "addressee", "night", "slot", "interrogation_kind", "evidence", "divination"}
                        if resolving else {"text", "selected_npc", "selected_cards"})
    if set(form) - allowed:
        raise ValueError("unknown_field")
    data = {"game_id": form.get("game_id")}
    if not resolving:
        data.update(expected_state_version=int(form.get("state_version", "-1")), text=form.get("text", ""))
        if form.get("selected_npc"):
            data["selected_npc"] = form["selected_npc"]
        data["selected_cards"] = []
        for value in form.getlist("selected_cards"):
            kind, separator, record = value.partition(":")
            if not separator:
                raise ValueError("invalid_evidence")
            data["selected_cards"].append({"type": kind, "record_id": record})
        return data
    reaction = form.get("reaction")
    data.update(attempt_id=form.get("attempt_id"), reaction=reaction)
    if reaction in ("confirm", "edit"):
        data.update(command_id=form.get("command_id"), expected_state_version=int(form.get("state_version", "-1")))
        if reaction == "confirm":
            data["chosen_index"] = int(form.get("chosen_index", "-1"))
        else:
            command_form = form.copy()
            for key in ("attempt_id", "reaction", "chosen_index", "retry_text"):
                command_form.pop(key, None)
            data["edited_command"] = _meeting_command_body(command_form)["command"]
    elif reaction == "retry":
        data["retry_text"] = form.get("retry_text", "")
    return data
