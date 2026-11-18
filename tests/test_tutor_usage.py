"""יאללה נדבר's usage in monitoring: conversations, tokens and estimated dollars by day and by model."""
from datetime import date
from zoneinfo import ZoneInfo

from app.auth import AuthUnavailable
from app.tutor_usage import cost, price_of, summarize
from tests.test_access_tiers import MANAGER, STUDENT, TierAuth, run

TZ = ZoneInfo("Asia/Jerusalem")
TODAY = date(2026, 11, 18)


def chat(when, model="claude-haiku-5-5", tin=10_000, tout=2_000, turns=5):
    return {"created_at": when, "model": model, "input_tokens": tin, "output_tokens": tout, "turns": turns}


def test_prices_follow_the_longest_matching_model_id():
    assert price_of("claude-haiku-5-5") == (0.10, 0.50) and price_of("claude-sonnet-5-5") == (2.00, 10.00)
    assert price_of("claude-sonnet-5") == (2.00, 10.00) and price_of("claude-opus-5-5") == (4.00, 20.00)
    assert price_of("gemini-3.1-flash-lite") is None and cost("gemini-3.1-flash-lite", 1, 1) is None
    assert cost("claude-haiku-5-5", 1_000_000, 1_000_000) == 0.60


def test_conversations_are_summed_by_local_day_and_model():
    rows = [chat("2026-11-18T07:00:00+00:00"), chat("2026-11-18T08:00:00+00:00", "claude-sonnet-5-5"),
            chat("2026-11-17T22:30:00+00:00"),   # already the 18th in Israel
            chat("2026-11-16T09:00:00Z", "gemini-3.1-flash-lite"),
            chat("2026-10-01T09:00:00Z"),        # before the period
            chat("not a time")]
    out = summarize(rows, 7, TODAY, TZ)
    assert (out["from_date"], out["to_date"], len(out["daily"])) == ("2026-11-12", "2026-11-18", 7)
    today, yesterday, before = out["daily"][:3]
    assert today["date"] == "2026-11-18" and today["conversations"] == 3 and today["turns"] == 15
    # two Haiku chats (10k in, 2k out) and one Sonnet: 2 x 0.002 + 0.04
    assert today["cost"] == round(2 * (10_000 * 0.10 + 2_000 * 0.50) / 1e6 + (10_000 * 2 + 2_000 * 10) / 1e6, 4)
    assert yesterday["conversations"] == 0 and before["unpriced"] == 1 and before["cost"] == 0
    assert out["total"]["conversations"] == 4 and out["total"]["unpriced"] == 1
    models = {m["model"]: m for m in out["models"]}
    assert models["claude-haiku-5-5"]["conversations"] == 2 and models["gemini-3.1-flash-lite"]["priced"] is False
    assert models["gemini-3.1-flash-lite"]["cost"] is None and out["estimate"] == "upper_bound"


class UsageAuth(TierAuth):
    def __init__(self, rows=None, missing=False):
        super().__init__()
        self.rows_, self.missing = rows or [], missing

    def tutor_usage_rows(self, since, limit=20_000):
        if self.missing:
            raise AuthUnavailable("supabase answered 404: relation tutor_chats does not exist")
        self.since = since
        return list(self.rows_)


def test_the_panel_reads_the_usage_and_a_student_cannot(monkeypatch):
    auth = UsageAuth([chat("2026-11-18T07:00:00Z")])

    async def steps(client):
        return {"usage": (await client.get("/api/admin/tutor-usage?days=7", headers=MANAGER)).json(),
                "student": (await client.get("/api/admin/tutor-usage", headers=STUDENT)).status_code,
                "too_long": (await client.get("/api/admin/tutor-usage?days=400", headers=MANAGER)).status_code}
    out = run(auth, steps, monkeypatch)
    assert out["usage"]["days"] == 7 and len(out["usage"]["daily"]) == 7 and "model_now" in out["usage"]
    assert out["student"] == 403 and out["too_long"] == 422

    async def missing(client):
        return (await client.get("/api/admin/tutor-usage", headers=MANAGER)).json()
    assert "create table public.tutor_chats" in run(UsageAuth(missing=True), missing, monkeypatch)["setup"]
