"""Shared user-session and permission resolution for browser and MCP callers."""
from __future__ import annotations

from .auth import AuthUnavailable, SupabaseAuth
from .library import ENTITLEMENT_PREFIX, LEGACY_SECTION, SECTION_ENTITLEMENTS, visible_sections


ROLES = frozenset({"student", "teacher", "flagger", "admin", "developer"})
DEFAULT_ROLES = ("student", "flagger")
# "library" is the permission from before the library had sections: it opens what the library held then, שלב א.
# A section's permission is "lib:<section id>" (library.SECTIONS); a section's covers its sub-sections.
ENTITLEMENTS = frozenset({"practice", "games", "tutor", "path", "library", *SECTION_ENTITLEMENTS})
DEFAULT_ENTITLEMENTS = frozenset({"practice", "games", "path", "library"})
LEGACY_LIBRARY = {"library": ENTITLEMENT_PREFIX + LEGACY_SECTION}

# Every account is in one of three tiers, and the tier alone decides what it may open:
#   מנהלים (admin)   everything but the lab (מעבדה), and the MCP connector;
#   מתרגלים (teacher) every exercise, game, path lesson and library section, but not ניהול and not the MCP;
#   תלמידים (student) תרגול, the games but יאללה נדבר, bug reports, and the path and library sections of their classes.
# The lab is the developers' alone ("developer" is a role on top of a tier, given only by a developer).
TIERS = ("admin", "teacher", "student")
TIER_NAMES = {"admin": "מנהלים", "teacher": "מתרגלים", "student": "תלמידים"}
TIER_ROLES = {"admin": ("admin", "teacher", "flagger"), "teacher": ("teacher", "flagger"),
              "student": ("student", "flagger")}
# what a saved student keeps outside every class (see `resolve_user_access`): not denied
TIER_ENTITLEMENTS = {"admin": (), "teacher": (), "student": ("practice", "games", "path")}
_STUDENT = frozenset({"practice.use", "exercises.use", "games.use", "path.use", "flags.create"})
_TEACHER = _STUDENT | {"exercises.use_all", "students.view", "tutor.use", "library.view"}
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


def tier_of(roles) -> str:
    """The tier of a set of roles: a developer counts as a manager."""
    roles = set(roles or ())
    return "admin" if roles & {"admin", "developer"} else "teacher" if "teacher" in roles else "student"


def class_sections(c: dict) -> list[str]:
    """The library sections a class opens to its students (ids from library.SECTIONS; a section covers its own).
    A class saved before classes had sections opens what the library held then, שלב א."""
    chosen = c.get("library_sections")
    return [LEGACY_SECTION] if chosen is None else [s for s in chosen if isinstance(s, str)]


def permissions_for(roles, sections: list[str]) -> set[str]:
    permissions = set().union(*(ROLE_PERMISSIONS[r] for r in set(roles) & ROLES))
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
    if classes and not mine and not teaches and not privileged and not entitlements:
        return {**user, "denied": True, "permissions": []}
    if not assigned:
        roles.update(DEFAULT_ROLES)
    everything = bool(roles & {"teacher", "admin", "developer"})
    if everything:
        sections = visible_sections(set(), True)
    elif classes:
        chosen = {ENTITLEMENT_PREFIX + s for c in mine for s in class_sections(c)}
        sections = visible_sections(chosen, False)
    else:   # no classes at all (an open deployment): what the library held before it had sections
        sections = [LEGACY_SECTION]
    permissions = permissions_for(roles, sections)
    return {**user, "token": token, "roles": sorted(roles), "entitlements": sorted(entitlements),
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
