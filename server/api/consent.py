"""계정 프로필에서 게임에 필요한 가명과 수집 동의를 읽는다."""


def player_context(user):
    """계정에서 발급한 가명과 동의 여부만 전달한다."""
    profile = getattr(user, "paldo_profile", None)
    if profile is None:
        return None
    return str(profile.player_pseudonym), profile.consented_at is not None
