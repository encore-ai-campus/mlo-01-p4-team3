"""승인된 참가자 한 명의 게임 Mongo 범위와 삭제 단계."""
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


def game_scope(database, player, *, session=None):
    """판 ID와 광고 가명, 삭제 전후에 검증할 건수를 조회한다."""
    games = list(database.games.find({"state.meta.player_pseudonym": player}, {"_id": 1},
                                    session=session).limit(10001))
    if len(games) > 10000:
        raise ValueError("privacy_game_scope_too_large")
    game_ids = sorted(row["_id"] for row in games)
    identities = database.b_ad_identities.find({"player_pseudonym": player},
                                              {"session_pseudonym": 1}, session=session)
    return {
        "game_ids": game_ids,
        "ad_sessions": sorted({row["session_pseudonym"] for row in identities}),
        "progress": database.progress.count_documents({"_id": player}, session=session),
        "interactions": database.b_interactions.count_documents({"_id": {"$in": game_ids}}, session=session),
        "test_sessions": database.b_test_sessions.count_documents({"participants": player}, session=session),
    }


def purge_games(database, player, expected_game_ids):
    """동일한 승인 범위의 게임·상호작용·진척을 Mongo 트랜잭션으로 삭제한다."""
    ids = sorted(set(expected_game_ids))

    def purge(session):
        before = game_scope(database, player, session=session)
        if not set(before["game_ids"]).issubset(ids):
            raise ValueError("privacy_game_scope_changed")
        by_id = {"_id": {"$in": before["game_ids"]}}
        counts = {
            "interactions": database.b_interactions.delete_many(by_id, session=session).deleted_count,
            "progress": database.progress.delete_one({"_id": player}, session=session).deleted_count,
            "test_sessions": database.b_test_sessions.update_many(
                {"participants": player}, {"$pull": {"participants": player}}, session=session
            ).modified_count,
            "games": database.games.delete_many(
                by_id | {"state.meta.player_pseudonym": player}, session=session
            ).deleted_count,
        }
        return counts

    with database.client.start_session() as session:
        return session.with_transaction(purge, read_concern=ReadConcern("snapshot"),
                                        write_concern=WriteConcern("majority"))
