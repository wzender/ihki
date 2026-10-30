"""get_game_popularity: what is measured, who is counted, and what is withheld."""
from datetime import date, datetime, timezone

import pytest

from app.auth import AuthUnavailable, SupabaseAuth
from app.monitoring import MonitoringError, game_popularity


TODAY = date(2026, 10, 30)
RANGE = {"from_date": "2026-10-02", "to_date": "2026-10-29", "today": TODAY,
         "retrieved_at": datetime(2026, 10, 30, 8, 0, tzinfo=timezone.utc)}
ADMIN = {"id": "admin", "roles": ["admin"], "subject": "ערבית", "permissions": ["management.access"]}
BUBBLE = "game-yalla-verb"


def stamp(day: int, hour: int = 9) -> str:
    return f"2026-10-{day:02d}T{hour:02d}:00:00+00:00"


class GameAuth:
    """Class A has s1–s5, class B has s4–s8 (s4 and s5 are in both). The math class is outside the admin's subject."""

    def __init__(self, rows=None, failures=None):
        self.class_rows = [
            {"id": 1, "name": "א", "subject": "ערבית", "emails": [f"s{i}@example.com" for i in range(1, 6)]},
            {"id": 2, "name": "ב", "subject": "ערבית", "emails": [f"s{i}@example.com" for i in range(4, 9)]},
            {"id": 3, "name": "מתמטיקה", "subject": "מתמטיקה", "emails": ["m@example.com"]},
        ]
        self.rows = {"game_attempts": [], "verb_game_events": [], "tutor_chats": [], **(rows or {})}
        self.failures = failures or {}
        self.calls = []

    def access_for(self, user_id, fresh=False):
        return {}

    def classes(self, fresh=False):
        return list(self.class_rows)

    def users(self):
        return [*({"id": f"u{i}", "email": f"s{i}@example.com"} for i in range(1, 9)),
                {"id": "um", "email": "m@example.com"}]

    def scoped_game_rows(self, source, ids, since, until, *, cats=()):
        self.calls.append((source, frozenset(ids), cats))
        failure = self.failures.get(source)
        if isinstance(failure, Exception):
            raise failure
        rows = self.rows[source]
        if source == "game_attempts":
            rows = [row for row in rows if row["cat"] in cats]
        return list(rows), failure == "truncated"


def bubble(user, day, row_id, cat=BUBBLE):
    return {"id": row_id, "user_id": user, "cat": cat, "created_at": stamp(day)}


def verb(user, day, row_id, level):
    return {"id": row_id, "actor_id": user, "created_at": stamp(day), "level": None if level is None else str(level)}


def chat(user, day, row_id, turns=4):
    return {"id": f"c{row_id}", "user_id": user, "created_at": stamp(day), "turns": turns}


def game(result, game_id):
    return next(item for item in result["games"] if item["game_id"] == game_id)


def ranked_ids(result, ranking):
    return [row["game_id"] for row in result["rankings"][ranking]]


# --- authorization -------------------------------------------------------------------------------------------------

def test_requires_management_permission_and_a_visible_class():
    auth = GameAuth()
    with pytest.raises(MonitoringError, match="אין הרשאת ניהול"):
        game_popularity(auth, {"id": "t", "roles": ["teacher"], "permissions": ["students.view"]}, **RANGE)
    for class_id in (3, 999):   # out of the admin's subject, and unknown: the same answer
        with pytest.raises(MonitoringError, match="הכיתה אינה זמינה"):
            game_popularity(auth, ADMIN, class_id=class_id, **RANGE)
    assert auth.calls == []


def test_reads_only_scoped_learners_and_ignores_rows_of_anyone_else():
    rows = {"game_attempts": [bubble(u, 5, i) for i, u in enumerate(["u1", "u2", "u3", "u4", "um"], 1)]
            + [bubble("u5", 6, 9)]}
    auth = GameAuth(rows)
    result = game_popularity(auth, ADMIN, class_id=1, **RANGE)
    assert result["class_id"] == 1
    assert all(ids == {"u1", "u2", "u3", "u4", "u5"} for _, ids, _ in auth.calls)
    assert game(result, BUBBLE)["distinct_active_players"] == 5   # "um" is outside the scope


def test_developer_sees_every_class():
    auth = GameAuth()
    developer = {"id": "dev", "roles": ["developer"], "permissions": ["*"]}
    game_popularity(auth, developer, **RANGE)
    assert auth.calls[0][1] == {f"u{i}" for i in range(1, 9)} | {"um"}


# --- distinct players and overlapping classes ----------------------------------------------------------------------

def test_a_learner_in_two_classes_and_a_repeated_row_count_once():
    # u4 and u5 are on both rosters; u1–u3 only on A, u6–u8 only on B.
    rows = {"game_attempts": [bubble("u4", 3, 1), bubble("u4", 3, 1), bubble("u4", 4, 2), bubble("u5", 4, 3),
                              bubble("u1", 5, 4), bubble("u1", 6, 5), bubble("u2", 5, 6), bubble("u2", 7, 7),
                              bubble("u3", 5, 8), bubble("u6", 6, 9), bubble("u6", 7, 10), bubble("u7", 6, 11),
                              bubble("u7", 7, 12), bubble("u8", 6, 13)]}
    auth = GameAuth(rows)
    result = game_popularity(auth, ADMIN, **RANGE)
    entry = game(result, BUBBLE)
    # Each source is read once for the union of both rosters, never once per class.
    assert [source for source, _, _ in auth.calls] == ["game_attempts", "verb_game_events", "tutor_chats"]
    assert entry["status"] == "complete"
    assert entry["distinct_active_players"] == 8         # not 10: u4 and u5 count once
    assert entry["activity_count"] == 13                 # the duplicated row id 1 is counted once
    assert entry["player_days"] == 13
    assert entry["returning_players"] == 5               # u1, u2, u4, u6, u7


def test_returning_players_are_learners_with_two_or_more_local_days():
    rows = {"game_attempts": [bubble(u, d, f"{u}{d}") for u in ("u1", "u2", "u3") for d in (3, 4)]}
    entry = game(game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE), BUBBLE)
    assert entry["distinct_active_players"] == 3 and entry["returning_players"] == 3 and entry["player_days"] == 6
    # One returning learner among five would single that learner out.
    rows = {"game_attempts": [bubble(u, 3, u) for u in ("u1", "u2", "u3", "u4", "u5")] + [bubble("u1", 4, "x")]}
    entry = game(game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE), BUBBLE)
    assert entry["distinct_active_players"] == 5
    assert entry["returning_players"] is None and entry["returning_players_status"] == "suppressed"


def test_local_calendar_days_follow_the_requested_time_zone():
    # 22:30 UTC on 10-05 is already 10-06 in Israel, so the learner played on two local days.
    rows = {"game_attempts": [bubble(u, 5, f"{u}a") for u in ("u1", "u2", "u3")]
            + [{"id": "late", "user_id": "u1", "cat": BUBBLE, "created_at": "2026-10-05T22:30:00+00:00"},
               bubble("u2", 5, "u2b")]}
    israel = game(game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE), BUBBLE)
    utc = game(game_popularity(GameAuth(rows), ADMIN, class_id=1, timezone_name="UTC", **RANGE), BUBBLE)
    assert israel["player_days"] == 4 and utc["player_days"] == 3
    with pytest.raises(MonitoringError, match="timezone"):
        game_popularity(GameAuth(), ADMIN, timezone_name="Mars/Base", **RANGE)


# --- missing tracking, missing data and zero -------------------------------------------------------------------------

def test_device_only_games_are_not_measurable_and_never_ranked_as_zero():
    result = game_popularity(GameAuth(), ADMIN, **RANGE)
    for game_id, reason in (("verb-memory", "result_stored_on_device_only"), ("falling-letters", "no_server_record")):
        entry = game(result, game_id)
        assert entry["status"] == "not_tracked" and entry["reason"] == reason
        assert entry["popularity_measurable"] is False and "distinct_active_players" not in entry
        assert game_id not in ranked_ids(result, "by_distinct_players")
        assert game_id not in ranked_ids(result, "by_activity_volume")
    gaps = {gap["game_id"]: gap for gap in result["tracking_gaps"]}
    assert set(gaps) == {"verb-memory", "falling-letters"}
    assert all(gap["historical_usage_recoverable"] is False for gap in gaps.values())
    assert result["answer"]["conclusive_among_all_games"] is False


def test_zero_activity_is_ranked_as_zero_and_differs_from_missing_data():
    zero = game_popularity(GameAuth(), ADMIN, **RANGE)
    assert zero["status"] == "no_activity"
    assert game(zero, "yalla-nedaber")["status"] == "no_activity"
    assert game(zero, "yalla-nedaber")["distinct_active_players"] == 0
    assert "yalla-nedaber" in ranked_ids(zero, "by_distinct_players")
    assert {row["rank"] for row in zero["rankings"]["by_distinct_players"]} == {1}
    assert zero["answer"]["most_distinct_players"] == []   # nobody leads with zero players

    missing = game_popularity(GameAuth(failures={"tutor_chats": AuthUnavailable("supabase answered 404: none")}),
                              ADMIN, **RANGE)
    entry = game(missing, "yalla-nedaber")
    assert entry["status"] == "unavailable" and entry["reason"] == "source_not_installed"
    assert "distinct_active_players" not in entry
    assert "yalla-nedaber" not in ranked_ids(missing, "by_distinct_players")
    assert missing["status"] == "partial" and missing["answer"]["conclusive_among_tracked_games"] is False
    assert {"source": "tutor_chats", "status": "unavailable", "reason": "source_not_installed"} in missing["data_sources"]


def test_every_source_unavailable_is_not_reported_as_no_activity():
    failures = {name: AuthUnavailable("timeout") for name in ("game_attempts", "verb_game_events", "tutor_chats")}
    result = game_popularity(GameAuth(failures=failures), ADMIN, **RANGE)
    assert result["status"] == "unavailable"
    assert result["rankings"] == {"by_distinct_players": [], "by_activity_volume": []}
    assert result["overall"] is None


def test_truncated_and_untimed_rows_make_a_source_unavailable_instead_of_undercounting():
    truncated = game_popularity(GameAuth(failures={"game_attempts": "truncated"}), ADMIN, **RANGE)
    assert game(truncated, BUBBLE)["reason"] == "row_limit_exceeded"
    untimed = {"tutor_chats": [chat("u1", 5, 1), {"id": "c2", "user_id": "u2", "created_at": None, "turns": 1}]}
    result = game_popularity(GameAuth(untimed), ADMIN, **RANGE)
    assert game(result, "yalla-nedaber")["reason"] == "unreliable_timestamps"


def test_verb_events_without_a_level_make_both_verb_games_not_attributable():
    players = ("u1", "u2", "u3", "u4")
    rows = {"verb_game_events": [verb(u, 5, i, None) for i, u in enumerate(players)]
            + [verb(u, 6, 10 + i, 7) for i, u in enumerate(players)]}
    result = game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE)
    for game_id in ("verb-game", "eitan-verb-builder"):
        entry = game(result, game_id)
        assert entry["status"] == "not_attributable" and entry["popularity_measurable"] is False
        assert game_id not in ranked_ids(result, "by_distinct_players")
    [combined] = result["unattributed_activity"]
    assert combined["status"] == "complete" and combined["distinct_active_players"] == 4
    assert combined["activity_count"] == 8


def test_levels_split_verb_events_between_the_two_verb_games():
    rows = {"verb_game_events": [verb(u, 5, f"{u}a", 1) for u in ("u1", "u2", "u3")]
            + [verb(u, 6, f"{u}b", 3) for u in ("u1", "u2")]
            + [verb(u, 7, f"{u}c", 8) for u in ("u1", "u2", "u3", "u4", "u5")],
            "game_attempts": [bubble("u4", 8, "s1", cat="verb-game")]}
    result = game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE)
    verb_game, eitan = game(result, "verb-game"), game(result, "eitan-verb-builder")
    assert verb_game["distinct_active_players"] == 4 and verb_game["activity_count"] == 6   # the spoken answer of u4
    assert verb_game["coverage"] == "partial"
    assert eitan["distinct_active_players"] == 5 and eitan["activity_count"] == 5
    assert result["unattributed_activity"] == []


# --- privacy -------------------------------------------------------------------------------------------------------

def test_small_groups_are_suppressed_everywhere_and_totals_are_withheld():
    rows = {"game_attempts": [bubble(u, 5, f"{u}{d}") for u in ("u1", "u2") for d in range(3)],
            "tutor_chats": [chat(u, d, f"{u}{d}") for u in ("u1", "u2", "u3") for d in (3, 4)]}
    result = game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE)
    hidden = game(result, BUBBLE)
    assert hidden["status"] == "suppressed" and hidden["reason"] == "privacy_threshold"
    assert not {"distinct_active_players", "player_days", "activity_count", "sessions"} & set(hidden)
    assert BUBBLE not in ranked_ids(result, "by_distinct_players") + ranked_ids(result, "by_activity_volume")
    assert result["overall"] is None            # a total across games would expose the suppressed one
    assert {"game_id": BUBBLE, "game_name": hidden["game_name"], "status": "suppressed",
            "reason": "privacy_threshold"} in result["excluded_from_ranking"]
    assert game(result, "yalla-nedaber")["distinct_active_players"] == 3


def test_complement_of_a_single_class_is_suppressed_in_the_all_classes_answer():
    # Class A alone shows 3 players. With all classes there are 4; subtracting would reveal the one player of B.
    rows = {"game_attempts": [bubble(u, 5, f"{u}{d}") for u in ("u1", "u2", "u3") for d in (5, 6)]
            + [bubble("u7", 6, "u7")]}
    alone = game(game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE), BUBBLE)
    everything = game(game_popularity(GameAuth(rows), ADMIN, **RANGE), BUBBLE)
    assert alone["status"] == "complete" and alone["distinct_active_players"] == 3
    assert everything["status"] == "suppressed" and everything["reason"] == "privacy_threshold_complement"
    assert "distinct_active_players" not in everything


def test_overlapping_members_do_not_count_as_the_rest_of_the_scope():
    # u4 and u5 are in both classes; removing class A leaves no one, so nothing is suppressed.
    rows = {"game_attempts": [bubble(u, d, f"{u}{d}") for u in ("u1", "u2", "u3", "u4", "u5") for d in (5, 6)]}
    entry = game(game_popularity(GameAuth(rows), ADMIN, **RANGE), BUBBLE)
    assert entry["status"] == "complete" and entry["distinct_active_players"] == 5


def test_output_carries_no_identities_or_raw_events():
    rows = {"game_attempts": [bubble(u, 5, f"{u}{d}") for u in ("u1", "u2", "u3") for d in (5, 6)],
            "tutor_chats": [chat(u, 7, u) for u in ("u1", "u2", "u3", "u4", "u5")]}
    text = str(game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE))
    for secret in ("@example.com", "'u1'", "u2", "cu1", "created_at", "actor_id", "messages", "2026-10-05T"):
        assert secret not in text


# --- rankings ------------------------------------------------------------------------------------------------------

def test_most_players_and_most_activity_can_be_different_games():
    rows = {"game_attempts": [bubble(u, 5, u) for u in ("u1", "u2", "u3", "u4", "u5")],
            "tutor_chats": [chat(u, d, f"{u}{d}", turns=6) for u in ("u1", "u2", "u3") for d in (3, 4, 5, 6)]}
    result = game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE)
    assert ranked_ids(result, "by_distinct_players")[0] == BUBBLE
    assert ranked_ids(result, "by_activity_volume")[0] == "yalla-nedaber"
    assert result["answer"]["most_distinct_players"] == [BUBBLE]
    assert result["answer"]["most_activity_volume"] == ["yalla-nedaber"]
    assert result["answer"]["leaders_differ"] is True
    tutor = game(result, "yalla-nedaber")
    assert tutor["sessions"] == 12 and tutor["sessions_unit"] == "conversations_started"
    assert tutor["activity_count"] == 72 and tutor["player_days"] == 12 and tutor["returning_players"] == 3
    assert game(result, BUBBLE)["sessions"] is None and game(result, BUBBLE)["sessions_reason"] == "not_recorded"
    assert result["overall"]["distinct_players_any_tracked_game"] == 5


def test_ties_share_a_rank():
    rows = {"game_attempts": [bubble(u, d, f"{u}{d}") for u in ("u1", "u2", "u3") for d in (5, 6)],
            "tutor_chats": [chat(u, d, f"{u}{d}") for u in ("u1", "u2", "u3") for d in (5, 6)]}
    result = game_popularity(GameAuth(rows), ADMIN, class_id=1, **RANGE)
    top = [row for row in result["rankings"]["by_distinct_players"] if row["rank"] == 1]
    assert {row["game_id"] for row in top} == {BUBBLE, "yalla-nedaber"}
    assert result["answer"]["most_distinct_players"] == sorted([BUBBLE, "yalla-nedaber"])
    assert result["rankings"]["by_distinct_players"][2]["rank"] == 3


def test_period_and_provenance_are_reported():
    result = game_popularity(GameAuth(), ADMIN, **RANGE)
    assert (result["from_date"], result["to_date"], result["days_included"]) == ("2026-10-02", "2026-10-29", 28)
    assert result["timezone"] == "Asia/Jerusalem" and result["retrieved_at"] == "2026-10-30T08:00:00Z"
    assert [source["source"] for source in result["data_sources"]] == ["attempts", "system_events", "tutor_chats"]
    assert set(result["definitions"]) >= {"distinct_active_players", "player_days", "returning_players", "sessions"}
    with pytest.raises(MonitoringError):
        game_popularity(GameAuth(), ADMIN, from_date="2026-10-02", to_date="2026-10-31", today=TODAY)


# --- storage query --------------------------------------------------------------------------------------------------

def test_storage_reads_minimal_columns_pages_and_signals_truncation():
    auth = SupabaseAuth("https://example.supabase.co", "public", secret="secret")
    paths = []

    def admin(path, *args, **kwargs):
        paths.append(path)
        return [{"id": len(paths)}] * (1000 if len(paths) == 1 else 2)

    auth._admin = admin
    since, until = datetime(2026, 10, 2, tzinfo=timezone.utc), datetime(2026, 10, 30, tzinfo=timezone.utc)
    rows, truncated = auth.scoped_game_rows("tutor_chats", {"u1"}, since, until, limit=1001)
    assert len(rows) == 1001 and truncated is True and len(paths) == 2
    assert "select=id%2Cuser_id%2Ccreated_at%2Cturns" in paths[0]
    assert "messages" not in paths[0] and "email" not in paths[0]
    paths.clear()
    auth.scoped_game_rows("verb_game_events", {"u1"}, since, until)
    assert "kind=eq.game" in paths[0] and "actor_id=in.%28u1%29" in paths[0]
    assert auth.scoped_game_rows("game_attempts", {"u1"}, since, until) == ([], False)   # no game ids: no read
    assert auth.scoped_game_rows("tutor_chats", set(), since, until) == ([], False)

