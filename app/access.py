"""Shared user-session and permission resolution for browser and MCP callers."""
from __future__ import annotations

import os

from .auth import AuthUnavailable, SupabaseAuth
from . import library
from .phrases import LESSONS
from .library import ENTITLEMENT_PREFIX, LEGACY_SECTION, SECTION_ENTITLEMENTS, SEED_HOLDERS, visible_sections


ROLES = frozenset({"student", "teacher", "flagger", "admin", "developer"})
DEFAULT_ROLES = ("student", "flagger")
# "library" is the permission from before the library had sections: it opens what the library held then, שלב א.
# A section's permission is "lib:<section id>" (library.SECTIONS); a section's covers its sub-sections.
_LESSON_LEVEL = {lesson["id"]: lesson["level"] for lesson in LESSONS}
ENTITLEMENTS = frozenset({"practice", "games", "tutor", "path", "library", *SECTION_ENTITLEMENTS})
DEFAULT_ENTITLEMENTS = frozenset({"practice", "games", "path", "library"})
LEGACY_LIBRARY = {"library": ENTITLEMENT_PREFIX + LEGACY_SECTION}

# Every account is in one of two tiers: managers and students. The managers' roles give ניהול and the MCP; the learner
# features (FEATURES below) each have a default per tier, which a manager may turn on or
# off for a whole tier (the feature_access table, through the MCP) and the server's FEATURE_PINS may fix. An account's
# own exceptions (clean_overrides) come last and win. The lab is the developers' alone ("developer" is a role on top
# of a tier, given only by a developer), and a developer keeps every feature whatever the tiers' switches say.
# There is no tier of its own for the guides (מתרגלים): a guide is a student of their classes, and sees the app exactly as
# its students do. An account whose roles are "teacher" without "admin" (the old מתרגלים tier) is a student.
TIERS = ("admin", "student")
TIER_NAMES = {"admin": "מנהלים", "student": "תלמידים"}
TIER_ROLES = {"admin": ("admin", "teacher", "flagger"), "student": ("student", "flagger")}
# what a saved student gets; outside every class a student is still denied (see `resolve_user_access`)
TIER_ENTITLEMENTS = {"admin": (), "student": ("practice", "games", "path")}
_STUDENT = frozenset({"flags.create"})   # the learner features come from FEATURES, by tier
_TEACHER = _STUDENT | {"students.view", "library.view"}
_ADMIN = _TEACHER | {"management.access", "users.manage", "classes.manage", "content.manage", "flags.review",
                     "mcp.use"}
ROLE_PERMISSIONS = {
    "student": _STUDENT,   # library.view is added when one of their classes opens a library section
    "teacher": _STUDENT,   # with "admin" a manager's; alone, the old מתרגלים tier: a student's
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
                     "games.camel": ("games.camel",), "path.all_lessons": ("exercises.use_all",),
                     "library.all_sections": ()}   # every library section, instead of the classes' own


# The learner features a tier has or not: key -> (Hebrew name, default per tier, the permissions it gives).
# library.all_sections gives no permission: it opens every library section instead of the classes' own.
_ALL = {"admin": True, "student": True}
FEATURES: dict[str, tuple[str, dict[str, bool], tuple[str, ...]]] = {
    "games.yalla_nedaber": ("יאללה נדבר", {"admin": True, "student": False}, ("tutor.use",)),
    "games.all": ("משחקים (כל לשונית המשחקים)", _ALL, ("games.use",)),
    "games.verb": ("פועל יוצא", _ALL, ("games.verb",)),
    "games.eitan": ("בונים פועל עם איתן", _ALL, ("games.eitan",)),
    "games.falling": ("אותיות נופלות", _ALL, ("games.falling",)),
    "games.bubble": ("פועל זמני", _ALL, ("games.bubble",)),
    "games.camel": ("מרוץ גמלים", _ALL, ("games.camel",)),
    "practice": ("תרגול", _ALL, ("practice.use", "exercises.use")),
    "path": ("מסלול", _ALL, ("path.use",)),
    "path.all_lessons": ("כל השיעורים (לא רק של הכיתות)", {"admin": True, "student": False},
                         ("exercises.use_all",)),
    "library.all_sections": ("כל מדורי המערכת הדיגיטלית (לא רק של הכיתות)",
                             {"admin": True, "student": False}, ()),
}
# a per-account exception (clean_overrides) of the same thing as a tier's feature
OVERRIDE_OF_FEATURE = {"games.yalla_nedaber": "tutor", "games.all": "games", "practice": "practice", "path": "path",
                       **{k: k for k in ("games.verb", "games.eitan", "games.falling", "games.bubble", "games.camel",
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
# the column of an account's own exceptions (clean_overrides), set from the permission editor and through the MCP
MIGRATE_ACCESS_OVERRIDES = ("alter table public.user_access add column if not exists overrides jsonb not null "
                            "default '{}'::jsonb; notify pgrst, 'reload schema';")


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
        if key in OVERRIDE_FEATURES or (key.startswith("lib:") and key[4:] in SEED_HOLDERS) or \
                (key.startswith("level:") and 0 < len(key) - 6 <= 8 and key[6:].isalnum()):
            out[key] = given
    return out


def tier_of(roles) -> str:
    """The tier of a set of roles: a developer counts as a manager."""
    roles = set(roles or ())
    return "admin" if roles & {"admin", "developer"} else "student"


def class_sections(c: dict) -> list[str]:
    """The library sections a class opens to its students (ids from library.SECTIONS; a section covers its own).
    A class saved before classes had sections opens the sections of the levels it studies (what the library held then,
    שלב א, for a class whose lessons name no level)."""
    chosen = c.get("library_sections")
    if chosen is None:   # the section of each level the class studies, where there is one; else שלב א
        mine = {_LESSON_LEVEL[l] for l in c.get("lessons") or [] if l in _LESSON_LEVEL}
        own = [node["id"] for node in library.tree().walk() if node["level"] in mine]
        return own or [LEGACY_SECTION]
    return [s for s in chosen if isinstance(s, str)]


def class_library(c: dict) -> list[str]:
    """The sections that hold videos which a class opens to its students: those it chose, except that the sections of
    the lessons (שלב א ‣ שיעור 1, שלב ב ‣ שיעור 10…) follow what the class studies. A class that opens any level's section
    sees the lessons' sections of the levels its own lessons belong to, and of a level whose section it was given by
    name beside its own (שלב א, for a class of שלב ב to go back to), and no other level's. A class whose lessons name no
    level keeps what it chose."""
    seen = set(visible_sections({ENTITLEMENT_PREFIX + s for s in class_sections(c)}, False))
    by_level = library.lesson_sections()
    levels = {_LESSON_LEVEL[l] for l in c.get("lessons") or [] if l in _LESSON_LEVEL}
    tree = library.tree()
    named = {tree.nodes[s]["level"] for s in c.get("library_sections") or [] if isinstance(s, str) and s in tree.nodes}
    if levels & named:   # its own level's section is among those it was given: the other levels named there are meant
        levels = levels | (named - {""})
    opened = any(seen & set(tree.covered(node["id"])) for node in tree.walk() if node["level"])
    if levels and opened:
        for level, ids in by_level.items():
            seen = seen | ids if level in levels else seen - ids
    return [leaf for leaf in library.holders() if leaf in seen]


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
    library.refresh(a, fresh=fresh_permissions)   # the sections a class or an account opens include the sub-sections added since
    classes = a.classes()
    teaches = [c for c in classes if email in (c.get("teachers") or [])]
    # a class's own teacher (a guide) studies in it as its students do: the same lessons, library and home
    mine = [c for c in classes if email in c["emails"] or c in teaches]
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
    # Outside every class only staff, or an account given something of its own in the editor (a ✓), get in. A saved
    # student row (its default "flagger" role and entitlements) is not enough: that let classless accounts see the
    # whole path.
    privileged = roles & {"admin", "developer"} or email in getattr(a, "tutors", ())
    overrides = clean_overrides((assigned or {}).get("overrides"))
    if classes and not mine and not teaches and not privileged and not any(overrides.values()):
        return {**user, "denied": True, "permissions": [], "overrides": overrides}
    if not assigned:
        roles.update(DEFAULT_ROLES)
    features = effective_features(feature_table(a))[tier_of(roles)]
    everything = "developer" in roles or overrides.get("library.all_sections", features["library.all_sections"]["enabled"])
    if everything:
        sections = visible_sections(set(), True)
    elif classes:
        seen = {leaf for c in mine for leaf in class_library(c)}
        sections = [leaf for leaf in library.holders() if leaf in seen]
    else:   # no classes at all (an open deployment): what the library held before it had sections
        sections = [s for s in [LEGACY_SECTION] if s in library.holders()]
    for key, given in overrides.items():   # the account's own exceptions to the library sections
        if key.startswith("lib:"):
            named = set(library.covered(key[4:]))   # a section's own, and its sub-sections' (they have no exception of their own)
            sections = [leaf for leaf in library.holders() if (leaf in sections or (given and leaf in named))
                        and not (not given and leaf in named)]
    permissions = permissions_for(roles, sections, features)
    for key, given in overrides.items():   # and to the features
        for permission in OVERRIDE_FEATURES.get(key, ()):
            (permissions.add if given else permissions.discard)(permission)
    return {**user, "token": token, "overrides": overrides,
            "level_overrides": {k[6:]: v for k, v in overrides.items() if k.startswith("level:")}, "roles": sorted(roles), "entitlements": sorted(entitlements),
            "library_sections": sections, "tier": tier_of(roles),
            "subject": str((assigned or {}).get("subject") or ""), "permissions": sorted(permissions),
            "teacher": bool(roles & {"admin", "developer"}),   # a manager: every lesson, no class (a class's own teacher is its student)
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


def deletable_account(a: SupabaseAuth, actor: dict, accounts: list[dict], who: str) -> dict:
    """The account (by email or id, from account_rows) that `actor` may delete, or AccessChangeError: only a student's
    account that no class has (on its roster or as its teacher), such as one opened with a typo; never one's own,
    a manager's, a developer's, or one the server's settings name. Checked again when the deletion is published."""
    wanted = str(who or "").strip().lower()
    target = next((u for u in accounts if wanted in (str(u["id"]).lower(), str(u["email"]).lower())), None)
    if target is None:
        raise AccessChangeError(404, "אין חשבון עם הכתובת הזו")
    if not actor or target["id"] == actor.get("id"):
        raise AccessChangeError(403, "אי אפשר למחוק את החשבון של עצמך")
    if target["fixed"]:
        raise AccessChangeError(403, "החשבון הזה מוגדר בהגדרות השרת ואי אפשר למחוק אותו")
    if target["developer"] or target["tier"] != "student":
        raise AccessChangeError(403, "אפשר למחוק רק חשבון של תלמיד. העבירו אותו קודם לתלמידים")
    email = str(target["email"]).lower()
    held = [c.get("name") or str(c.get("id")) for c in a.classes(fresh=True)
            if email in {str(e).lower() for e in (c.get("emails") or [])}
            or email in {str(e).lower() for e in (c.get("teachers") or [])}]
    if held:
        raise AccessChangeError(409, "החשבון רשום בכיתה (" + ", ".join(held) + "). הוציאו אותו קודם מהכיתה")
    return target


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
                 "games.bubble": "games.bubble", "games.camel": "games.camel", "path.all_lessons": "exercises.use_all",
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
                           "library.all_sections": set(library.leaf_sections()) <= set(me.get("library_sections") or []),
                           **{k: has(p) for k, p in flags.items()}}
    return out


def account_features(a: SupabaseAuth, account: dict, rows: list[dict], overrides: dict | None = None) -> dict:
    """What one account (a row of account_rows) gets of each learner feature, as the MCP shows it: {"denied": outside
    every class with nothing of its own, "features": {key: {"tier": its tier's value, "own": its own exception (True,
    False, or None when it has none), "enabled": what the account gets}}}. `overrides`, in place of the saved ones,
    shows what a change would give. The same resolution as at sign-in (resolve_user_access)."""
    saved = next((r for r in rows if r.get("user_id") == account["id"]), None)
    if overrides is None:
        overrides = clean_overrides((saved or {}).get("overrides"))
    else:
        rows = [r for r in rows if r.get("user_id") != account["id"]] + \
            [{**(saved or {"user_id": account["id"]}), "overrides": overrides}]
    me = resolve_user_access(_KnownAccess(a, rows), {"id": account["id"], "email": account.get("email") or ""})
    denied, perms = bool(me.get("denied")), set(me.get("permissions") or ())
    tier = effective_features(feature_table(a))[account["tier"]]
    out = {}
    for key, (_name, _defaults, gives) in FEATURES.items():
        own = overrides.get(OVERRIDE_OF_FEATURE[key])
        if denied:
            enabled = False
        elif "*" in perms:
            enabled = True
        elif gives:
            enabled = all(p in perms for p in gives)
        else:   # library.all_sections gives no permission
            enabled = tier[key]["enabled"] if own is None else own
        out[key] = {"tier": tier[key]["enabled"], "own": own, "enabled": enabled}
    return {"denied": denied, "features": out}


def change_tier(a: SupabaseAuth, actor: dict | None, target_id: str, tier: str, developer: bool | None = None) -> dict:
    """Puts an account in a tier (and, for a developer acting, gives or takes the lab). The checks of both the page and
    the MCP: nobody changes their own access; a server-set manager or developer stays one; only a developer touches the
    lab; the last developer stays. Returns {"target", "before", "after"}; raises AccessChangeError."""
    if tier not in TIERS:
        raise AccessChangeError(400, "התפקיד הוא אחד מ: מנהלים, תלמידים")
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
