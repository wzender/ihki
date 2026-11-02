"""Shared user-session and permission resolution for browser and MCP callers."""
from __future__ import annotations

import os

from .auth import AuthUnavailable, SupabaseAuth
from .library import ENTITLEMENT_PREFIX, LEAVES, LEGACY_SECTION, SECTION_ENTITLEMENTS, visible_sections


ROLES = frozenset({"student", "teacher", "flagger", "admin", "developer"})
DEFAULT_ROLES = ("student", "flagger")
# "library" is the permission from before the library had sections: it opens what the library held then, שלב א.
# A section's permission is "lib:<section id>" (library.SECTIONS); a section's covers its sub-sections.
ENTITLEMENTS = frozenset({"practice", "games", "tutor", "path", "library", *SECTION_ENTITLEMENTS})
DEFAULT_ENTITLEMENTS = frozenset({"practice", "games", "path", "library"})
LEGACY_LIBRARY = {"library": ENTITLEMENT_PREFIX + LEGACY_SECTION}

# Every account is in one of three tiers. The tier's roles give ניהול (admin), the MCP (admin) and the panel's student
# views (teacher); the learner features (FEATURES below) each have a default per tier, which a manager may turn on or
# off for a whole tier (the feature_access table, through the MCP) and the server's FEATURE_PINS may fix. An account's
# own exceptions (clean_overrides) come last and win. The lab is the developers' alone ("developer" is a role on top
# of a tier, given only by a developer), and a developer keeps every feature whatever the tiers' switches say.
TIERS = ("admin", "teacher", "student")
TIER_NAMES = {"admin": "מנהלים", "teacher": "מתרגלים", "student": "תלמידים"}
TIER_ROLES = {"admin": ("admin", "teacher", "flagger"), "teacher": ("teacher", "flagger"),
              "student": ("student", "flagger")}
# what a saved student keeps outside every class (see `resolve_user_access`): not denied
TIER_ENTITLEMENTS = {"admin": (), "teacher": (), "student": ("practice", "games", "path")}
_STUDENT = frozenset({"flags.create"})   # the learner features come from FEATURES, by tier
_TEACHER = _STUDENT | {"students.view", "library.view"}
_ADMIN = _TEACHER | {"management.access", "users.manage", "classes.manage", "content.manage", "flags.review",
                     "mcp.use"}
ROLE_PERMISSIONS = {
    "student": _STUDENT,   # library.view is added when one of their classes opens a library section
    "teacher": _TEACHER,
    "flagger": frozenset({"flags.create"}),
    "admin": _ADMIN,
    "developer": frozenset({"*"}),
}
ENTITLEMENT_PERMISSIONS = {"practice": {"practice.use", "exercises.use"}, "games": {"games.use"},
                           "tutor": {"tutor.use"}, "path": {"path.use"}, "library": {"library.view"},
                           **{e: {"library.view"} for e in SECTION_ENTITLEMENTS}}


# One account's own exceptions to what its tier and classes give (the permission editor's cells): {key: True (given)
# or False (taken away)}. A key is "level:<level id>" (the path's lessons of that level), "lib:<leaf section id>", or
# one of OVERRIDE_FEATURES. ניהול and the lab are not among them: those go by the tier and the developer role alone.
OVERRIDE_FEATURES = {"practice": ("practice.use", "exercises.use"), "games": ("games.use",),
                     "tutor": ("tutor.use",), "path": ("path.use",),
                     "games.verb": ("games.verb",), "games.eitan": ("games.eitan",),
                     "games.falling": ("games.falling",), "games.bubble": ("games.bubble",),
                     "path.all_lessons": ("exercises.use_all",),
                     "library.all_sections": ()}   # every library section, instead of the classes' own


# The learner features a tier has or not: key -> (Hebrew name, default per tier, the permissions it gives).
# library.all_sections gives no permission: it opens every library section instead of the classes' own.
_ALL = {"admin": True, "teacher": True, "student": True}
FEATURES: dict[str, tuple[str, dict[str, bool], tuple[str, ...]]] = {
    "games.yalla_nedaber": ("יאללה נדבר", {"admin": True, "teacher": False, "student": False}, ("tutor.use",)),
    "games.all": ("משחקים (כל לשונית המשחקים)", _ALL, ("games.use",)),
    "games.verb": ("פועל יוצא", _ALL, ("games.verb",)),
    "games.eitan": ("בונים פועל עם איתן", _ALL, ("games.eitan",)),
    "games.falling": ("אותיות נופלות", _ALL, ("games.falling",)),
    "games.bubble": ("פועל זמני", _ALL, ("games.bubble",)),
    "practice": ("תרגול", _ALL, ("practice.use", "exercises.use")),
    "path": ("מסלול", _ALL, ("path.use",)),
    "path.all_lessons": ("כל השיעורים (לא רק של הכיתות)", {"admin": True, "teacher": True, "student": False},
                         ("exercises.use_all",)),
    "library.all_sections": ("כל מדורי הספרייה (לא רק של הכיתות)",
                             {"admin": True, "teacher": True, "student": False}, ()),
}
# a per-account exception (clean_overrides) of the same thing as a tier's feature
OVERRIDE_OF_FEATURE = {"games.yalla_nedaber": "tutor", "games.all": "games", "practice": "practice", "path": "path",
                       **{k: k for k in ("games.verb", "games.eitan", "games.falling", "games.bubble",
                                         "path.all_lessons", "library.all_sections")}}
CREATE_FEATURE_ACCESS = (
    "create table public.feature_access (tier text not null check (tier in ('admin','teacher','student')), "
    "feature_key text not null, enabled boolean not null, updated_by uuid, "
    "updated_at timestamptz not null default now(), primary key (tier, feature_key)); "
    "alter table public.feature_access enable row level security; "
    "create table public.feature_access_audit (id bigint generated always as identity primary key, "
    "tier text not null, feature_key text not null, old_enabled boolean not null, new_enabled boolean not null, "
    "changed_by uuid not null, created_at timestamptz not null default now()); "
    "alter table public.feature_access_audit enable row level security; notify pgrst, 'reload schema';")


def feature_pins(text: str | None = None) -> dict[tuple[str, str], bool]:
    """The server's fixed feature values, FEATURE_PINS="games.yalla_nedaber:student=off,practice:teacher=on" (tier
    "all" fixes every tier). Unknown keys, tiers and values are ignored."""
    text = os.environ.get("FEATURE_PINS", "") if text is None else text
    pins = {}
    for item in text.split(","):
        key, _, rest = item.strip().partition(":")
        tier, _, value = rest.partition("=")
        if key not in FEATURES or value.strip().lower() not in ("on", "off"):
            continue
        for t in TIERS if tier.strip() == "all" else [tier.strip()]:
            if t in TIERS:
                pins[(t, key)] = value.strip().lower() == "on"
    return pins


def feature_table(a) -> list[dict] | None:
    """The managers' switches ([{"tier", "feature_key", "enabled"}]); [] where none were set, and None when the table
    was not created (then every tier has its defaults)."""
    reader = getattr(a, "feature_rows", None)
    return reader() if reader else []


def effective_features(rows: list[dict] | None, pins: dict | None = None) -> dict[str, dict[str, dict]]:
    """{tier: {key: {"enabled", "default", "overridden", "fixed_by_server"}}}: the default, then the managers' switch,
    then the server's pin."""
    pins = feature_pins() if pins is None else pins
    set_ = {(r.get("tier"), r.get("feature_key")): bool(r.get("enabled")) for r in rows or []}
    out: dict[str, dict[str, dict]] = {}
    for tier in TIERS:
        out[tier] = {}
        for key, (_, defaults, _perms) in FEATURES.items():
            enabled = set_.get((tier, key), defaults[tier])
            fixed = (tier, key) in pins
            if fixed:
                enabled = pins[(tier, key)]
            out[tier][key] = {"enabled": enabled, "default": defaults[tier],
                              "overridden": (tier, key) in set_ and set_[(tier, key)] != defaults[tier],
                              "fixed_by_server": fixed}
    return out


def clean_overrides(value) -> dict[str, bool]:
    """The overrides kept in an access row, without anything unknown."""
    if not isinstance(value, dict):
        return {}
    out = {}
    for key, given in value.items():
        if not isinstance(key, str) or not isinstance(given, bool):
            continue
        if key in OVERRIDE_FEATURES or (key.startswith("lib:") and key[4:] in LEAVES) or \
                (key.startswith("level:") and 0 < len(key) - 6 <= 8 and key[6:].isalnum()):
            out[key] = given
    return out


def tier_of(roles) -> str:
    """The tier of a set of roles: a developer counts as a manager."""
    roles = set(roles or ())
    return "admin" if roles & {"admin", "developer"} else "teacher" if "teacher" in roles else "student"


def class_sections(c: dict) -> list[str]:
    """The library sections a class opens to its students (ids from library.SECTIONS; a section covers its own).
    A class saved before classes had sections opens what the library held then, שלב א."""
    chosen = c.get("library_sections")
    return [LEGACY_SECTION] if chosen is None else [s for s in chosen if isinstance(s, str)]


def permissions_for(roles, sections: list[str], features: dict[str, dict] | None = None) -> set[str]:
    """The roles' permissions, and those of the tier's features that are on (`features`: effective_features()[tier];
    None for the defaults)."""
    permissions = set().union(*(ROLE_PERMISSIONS[r] for r in set(roles) & ROLES))
    tier = tier_of(roles)
    for key, (_, defaults, perms) in FEATURES.items():
        if (features[key]["enabled"] if features else defaults[tier]):
            permissions.update(perms)
    if sections:
        permissions.add("library.view")
    return permissions


def normalize_entitlements(entitlements) -> set[str]:
    """Known entitlements only, the old library permission written as the section it now means."""
    return {LEGACY_LIBRARY.get(e, e) for e in entitlements or ()} & ENTITLEMENTS


def resolve_access(a: SupabaseAuth, token: str, *, fresh_permissions: bool = False) -> dict | None:
    """Return the current application identity for a verified Supabase access token.

    ``fresh_permissions`` bypasses the short user-access cache. MCP uses it on every
    request so revoking management access takes effect immediately.
    """
    user = a.user(token)
    if user is None:
        return None
    return resolve_user_access(a, user, token=token, fresh_permissions=fresh_permissions)


def resolve_user_access(a: SupabaseAuth, user: dict, *, token: str = "",
                        fresh_permissions: bool = False) -> dict:
    """The application identity of an already-identified Supabase user ({"id", "email"})."""
    email = str(user.get("email") or "").strip().lower()
    classes = a.classes()
    mine = [c for c in classes if email in c["emails"]]
    teaches = [c for c in classes if email in (c.get("teachers") or [])]
    member = bool(mine or teaches or not classes)
    roles, entitlements, assigned = {"student"}, set(DEFAULT_ENTITLEMENTS) if member else set(), None
    if hasattr(a, "access_for"):
        try:
            assigned = a.access_for(user["id"], fresh=fresh_permissions)
        except TypeError:  # compatible with small test/deployment adapters predating ``fresh``
            assigned = a.access_for(user["id"])
        if assigned:
            roles = set(assigned.get("roles") or ["student"]) & ROLES
            entitlements = set(assigned.get("entitlements") or []) & ENTITLEMENTS
    if email in getattr(a, "teachers", ()):
        roles.update({"teacher", "admin", "flagger"})
    if email in getattr(a, "tutors", ()):
        roles.add("flagger")
    if email in getattr(a, "developers", ()):
        roles.add("developer")
    privileged = roles - {"student"}
    overrides = clean_overrides((assigned or {}).get("overrides"))
    if classes and not mine and not teaches and not privileged and not entitlements and not any(overrides.values()):
        return {**user, "denied": True, "permissions": [], "overrides": overrides}
    if not assigned:
        roles.update(DEFAULT_ROLES)
    features = effective_features(feature_table(a))[tier_of(roles)]
    everything = "developer" in roles or overrides.get("library.all_sections", features["library.all_sections"]["enabled"])
    if everything:
        sections = visible_sections(set(), True)
    elif classes:
        chosen = {ENTITLEMENT_PREFIX + s for c in mine for s in class_sections(c)}
        sections = visible_sections(chosen, False)
    else:   # no classes at all (an open deployment): what the library held before it had sections
        sections = [LEGACY_SECTION]
    for key, given in overrides.items():   # the account's own exceptions to the library sections
        if key.startswith("lib:"):
            sections = [leaf for leaf in LEAVES if (leaf in sections or (given and leaf == key[4:]))
                        and not (not given and leaf == key[4:])]
    permissions = permissions_for(roles, sections, features)
    for key, given in overrides.items():   # and to the features
        for permission in OVERRIDE_FEATURES.get(key, ()):
            (permissions.add if given else permissions.discard)(permission)
    return {**user, "token": token, "overrides": overrides,
            "level_overrides": {k[6:]: v for k, v in overrides.items() if k.startswith("level:")}, "roles": sorted(roles), "entitlements": sorted(entitlements),
            "library_sections": sections, "tier": tier_of(roles),
            "subject": str((assigned or {}).get("subject") or ""), "permissions": sorted(permissions),
            "teacher": bool(roles & {"teacher", "admin", "developer"}),
            "tutor": "flags.create" in permissions or "*" in permissions, "classes": mine, "teaches": teaches}


def has_permission(user: dict | None, permission: str) -> bool:
    permissions = set((user or {}).get("permissions", ()))
    return "*" in permissions or permission in permissions


class AccessChangeError(ValueError):
    """A refused permission change: ``status`` is the HTTP code the page gets, the message is safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def account_rows(a: SupabaseAuth, users: list[dict], rows: list[dict]) -> list[dict]:
    """Every account with its tier, as the permission editor and the MCP list them: id, email, name, tier, roles,
    developer (has the lab), fixed (its tier or lab is set by the server's settings, not here)."""
    assigned = {r["user_id"]: r for r in rows}
    out = []
    for account in users:
        row = assigned.get(account["id"])
        email = str(account.get("email") or "").lower()
        roles = set((row or {}).get("roles") or ["student"]) & ROLES if row else set(DEFAULT_ROLES)
        fixed = []
        if email in getattr(a, "teachers", ()):
            roles.update({"teacher", "admin", "flagger"}); fixed.append("tier")
        if email in getattr(a, "developers", ()):
            roles.add("developer"); fixed.append("developer")
        out.append({**account, "tier": tier_of(roles), "roles": sorted(roles), "developer": "developer" in roles,
                    "subject": str((row or {}).get("subject") or ""), "fixed": fixed})
    return out


class _KnownAccess:
    """`a` with every access row already read, so that resolving each account in the list asks Supabase nothing more."""

    def __init__(self, a: SupabaseAuth, rows: list[dict]):
        self._a, self._rows = a, {r["user_id"]: r for r in rows}

    def __getattr__(self, name):
        return getattr(self._a, name)

    def access_for(self, user_id: str, fresh: bool = False) -> dict | None:
        return self._rows.get(user_id)


def what_opens(a: SupabaseAuth, users: list[dict], rows: list[dict], levels_of, all_levels: list[str]) -> dict[str, dict]:
    """What each account may open now, as the permission editor shows it: {user id: {"levels": the course levels of
    the path (every level for a teacher, else those of their classes' lessons), "practice", "games", "tutor" (יאללה
    נדבר), "path", "management", "lab": bools, "library": the leaf sections they may watch}}. `levels_of(class)` gives a
    class's levels. The same resolution as at sign-in (resolve_user_access), so it is what the account gets."""
    known, out = _KnownAccess(a, rows), {}
    for user in users:
        me = resolve_user_access(known, {"id": user["id"], "email": user.get("email") or ""})
        perms = set(me.get("permissions") or ())
        has = lambda p: "*" in perms or p in perms
        flags = {"practice": "practice.use", "games": "games.use", "tutor": "tutor.use", "path": "path.use",
                 "games.verb": "games.verb", "games.eitan": "games.eitan", "games.falling": "games.falling",
                 "games.bubble": "games.bubble", "path.all_lessons": "exercises.use_all",
                 "management": "management.access", "lab": "lab.access"}
        if me.get("denied"):
            out[user["id"]] = {"levels": [], "library": [], "denied": True, "overrides": me.get("overrides") or {},
                               "library.all_sections": False, **{k: False for k in flags}}
            continue
        level_over = me.get("level_overrides") or {}
        levels = set(all_levels) if has("exercises.use_all") else \
            {lv for c in (me.get("classes") or []) for lv in levels_of(c)}
        levels = (levels | {lv for lv, given in level_over.items() if given}) - {lv for lv, given in level_over.items() if not given}
        out[user["id"]] = {"levels": [lv for lv in all_levels if lv in levels], "library": me.get("library_sections") or [],
                           "overrides": me.get("overrides") or {},
                           "library.all_sections": set(LEAVES) <= set(me.get("library_sections") or []),
                           **{k: has(p) for k, p in flags.items()}}
    return out


def change_tier(a: SupabaseAuth, actor: dict | None, target_id: str, tier: str, developer: bool | None = None) -> dict:
    """Puts an account in a tier (and, for a developer acting, gives or takes the lab). The checks of both the page and
    the MCP: nobody changes their own access; a server-set manager or developer stays one; only a developer touches the
    lab; the last developer stays. Returns {"target", "before", "after"}; raises AccessChangeError."""
    if tier not in TIERS:
        raise AccessChangeError(400, "התפקיד הוא אחד מ: מנהלים, מתרגלים, תלמידים")
    if not actor or target_id == actor.get("id"):
        raise AccessChangeError(403, "אי אפשר לשנות את ההרשאות של עצמך")
    is_developer = "*" in (actor.get("permissions") or ())
    users = a.users()
    target = next((u for u in users if u["id"] == target_id), None)
    if not target:   # signed up just now: the list is reused for a minute
        target = next((u for u in a.users(fresh=True) if u["id"] == target_id), None)
    if not target:
        raise AccessChangeError(404, "החשבון לא נמצא")
    email = target["email"].lower()
    old = a.access_for(target_id) if hasattr(a, "access_for") else None
    old_roles = set((old or {}).get("roles") or ["student"])
    was_developer = "developer" in old_roles or email in getattr(a, "developers", ())
    developer = was_developer if developer is None else bool(developer)
    if developer != was_developer and not is_developer:
        raise AccessChangeError(403, "רק מפתחים יכולים לתת או לקחת גישה למעבדה")
    if was_developer and not is_developer:
        raise AccessChangeError(403, "רק מפתחים יכולים לשנות הרשאות של מפתח")
    if email in getattr(a, "developers", ()) and not developer:
        raise AccessChangeError(403, "המפתח הזה מוגדר בהגדרות השרת")
    if email in getattr(a, "teachers", ()) and tier != "admin":
        raise AccessChangeError(403, "המנהל הזה מוגדר בהגדרות השרת (TEACHER_EMAILS)")
    if "developer" in old_roles and not developer:
        rows = a.access_rows() or []
        remaining = sum("developer" in (r.get("roles") or []) and r.get("user_id") != target_id for r in rows)
        remaining += sum(e != email for e in getattr(a, "developers", ()))
        if not remaining:
            raise AccessChangeError(409, "אי אפשר להסיר את המפתח האחרון")
    roles = sorted({*TIER_ROLES[tier], *(["developer"] if developer else [])})
    a.set_access(target, roles, list(TIER_ENTITLEMENTS[tier]), actor)
    return {"target": {"id": target["id"], "email": email}, "before": tier_of(old_roles if old else DEFAULT_ROLES),
            "after": tier, "developer": developer}
