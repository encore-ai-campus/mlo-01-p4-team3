"""B-09-02 부하의 해석·취소 요청. 공개 화면만 쓰고 게임 명령은 실행하지 않는다."""


def probe_intent(view, send):
    """같은 판 버전에서 해석하고 취소한다. HTTP·수명주기 위반 수를 반환한다.

    send(route, path, body)는 (상태 코드, JSON)을 반환하며 모든 요청 시간을 수집한다.
    향후 실제 엔진 부하도 공개 PlayerView JSON과 같은 함수를 사용할 수 있다.
    """
    game_id, version = view['game_id'], view['state_version']
    base = f'/games/{game_id}/utterances'
    status, parsed = send('utterance', base, {
        'game_id': game_id, 'expected_state_version': version, 'text': '지켜보겠소',
    })
    if (status != 200 or not parsed.get('attempt_id') or
            parsed.get('state_version') != version or parsed.get('needs_confirmation') is not True):
        return 1
    attempt = parsed['attempt_id']
    status, cancelled = send('cancel', base + '/' + attempt, {
        'game_id': game_id, 'attempt_id': attempt, 'reaction': 'cancel',
    })
    return int(status != 200 or cancelled.get('state_version') != version or
               cancelled.get('needs_confirmation') is not False)


def should_probe(view, command_number, every):
    """지정 간격의 말하기 가능한 단계에서만 해석을 섞는다. 기본 0은 기존 부하다."""
    return bool(every and command_number % every == 0 and view and
                view.get('phase') in {'round1', 'round2', 'interrogation', 'vote', 'runoff'})
