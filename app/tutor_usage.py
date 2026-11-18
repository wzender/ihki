"""What יאללה נדבר has cost: its conversations (table `tutor_chats`), their tokens and an estimate in dollars, by day
and by model, for the panel's monitoring. The estimate is an upper bound: a conversation's input tokens include the
ones read from the prompt cache, which bill at a tenth of the input price, and they are counted here at the full one.
A model whose price is not listed here (Gemini's, or one added later) is counted in tokens and left out of the dollars."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# dollars per million tokens (input, output), Anthropic's list prices; the longest prefix that matches wins
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-fable-5-2": (8.00, 40.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
}
MAX_DAYS = 90


def price_of(model: str) -> tuple[float, float] | None:
    model = str(model or "").strip()
    best = max((key for key in PRICES if model.startswith(key)), key=len, default=None)
    return PRICES[best] if best else None


def cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    price = price_of(model)
    return None if price is None else (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def summarize(rows: list[dict], days: int, today: date, tz: ZoneInfo) -> dict:
    """rows: model, input_tokens, output_tokens, turns, created_at of the conversations. Every day of the period is
    listed, newest first, the empty ones too."""
    start = today - timedelta(days=days - 1)
    by_day: dict[date, dict] = defaultdict(lambda: {"conversations": 0, "turns": 0, "input_tokens": 0,
                                                    "output_tokens": 0, "cost": 0.0, "unpriced": 0})
    by_model: dict[str, dict] = defaultdict(lambda: {"conversations": 0, "input_tokens": 0, "output_tokens": 0})
    for row in rows:
        try:
            when = datetime.fromisoformat(str(row.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        day = when.astimezone(tz).date()
        if not start <= day <= today:
            continue
        model, tin, tout = str(row.get("model") or "") or "unknown", _int(row.get("input_tokens")), _int(row.get("output_tokens"))
        d, m = by_day[day], by_model[model]
        d["conversations"] += 1; d["turns"] += _int(row.get("turns")); d["input_tokens"] += tin; d["output_tokens"] += tout
        m["conversations"] += 1; m["input_tokens"] += tin; m["output_tokens"] += tout
        spent = cost(model, tin, tout)
        if spent is None:
            d["unpriced"] += 1
        else:
            d["cost"] += spent
    models = [{"model": name, **value, "cost": cost(name, value["input_tokens"], value["output_tokens"]),
               "priced": price_of(name) is not None} for name, value in sorted(by_model.items(), key=lambda kv: -kv[1]["conversations"])]
    daily = [{"date": (today - timedelta(days=i)).isoformat(), **{k: (round(v, 4) if k == "cost" else v)
                                                                  for k, v in by_day[today - timedelta(days=i)].items()}}
             for i in range(days)]
    total = {key: sum(day[key] for day in daily) for key in ("conversations", "turns", "input_tokens", "output_tokens", "unpriced")}
    total["cost"] = round(sum(day["cost"] for day in daily), 4)
    return {"from_date": start.isoformat(), "to_date": today.isoformat(), "days": days, "daily": daily, "models": models,
            "total": total, "estimate": "upper_bound",
            "note": "הערכה לפי מחירון Anthropic. הטוקנים שנקראו מהמטמון נספרים במחיר מלא, ולכן החשבון בפועל נמוך יותר."}
