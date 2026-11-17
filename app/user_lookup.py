"""Finding one user, for a manager who answers "it doesn't let me in": every account and class-roster address that
matches what was typed (an email, a part of one, or a name), and the addresses one or two letters away from a typed
email (a second account opened with a typo). For each address: its account (tier, name, how it signs in, when it last
did), or none (a roster address nobody signed in with yet), and the classes whose roster has it."""

from __future__ import annotations

MAX_RESULTS = 20
NEAR = 2   # letters a long address may differ by and still be shown as possibly the same person's (a short one: 1)
SHORT = 12   # an address shorter than this is "near" at one letter's difference only: rina@x.com is not dana@x.com


def distance(a: str, b: str, limit: int = NEAR) -> int:
    """Edit distance between a and b, or limit + 1 once it is known to be more."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0], best = row[0], i, i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
            best = min(best, row[j])
        if best > limit:
            return limit + 1
    return row[-1]


def lookup(query: str, accounts: list[dict], classes: list[dict], signins: dict[str, dict] | None = None) -> dict:
    """accounts: account_rows' (id, email, name, tier); classes: id, name, emails; signins: by account id, providers,
    last_sign_in and created. Exact and containing matches first, then the near ones."""
    q = " ".join(str(query or "").lower().split())
    if len(q) < 3:
        return {"query": q, "results": [], "more": False}
    signins = signins or {}
    by_email = {str(a.get("email") or "").lower(): a for a in accounts if a.get("email")}
    rostered: dict[str, list[dict]] = {}
    for c in classes:
        for email in c.get("emails") or []:
            rostered.setdefault(str(email).lower(), []).append({"id": c.get("id"), "name": c.get("name")})
    found: dict[str, str] = {}   # email -> "match" | "near"
    for email in {*by_email, *rostered}:
        name = str((by_email.get(email) or {}).get("name") or "").lower()
        if q in email or (name and q in name):
            found[email] = "match"
        elif "@" in q and distance(q, email) <= (NEAR if len(q) >= SHORT else 1):
            found[email] = "near"
    ordered = sorted(found, key=lambda e: (e != q, found[e] != "match", e))
    results = []
    for email in ordered[:MAX_RESULTS]:
        account = by_email.get(email)
        seen = signins.get(account["id"], {}) if account else {}
        results.append({
            "email": email, "how": "exact" if email == q else found[email],
            "account": {"id": account["id"], "name": account.get("name") or "", "tier": account.get("tier") or "",
                        "providers": list(seen.get("providers") or []), "last_sign_in": seen.get("last_sign_in") or "",
                        "created": seen.get("created") or ""} if account else None,
            "classes": rostered.get(email, [])})
    return {"query": q, "results": results, "more": len(ordered) > MAX_RESULTS}
