"""Shared user-session and permission resolution for browser and MCP callers."""
from __future__ import annotations

from .auth import AuthUnavailable, SupabaseAuth


ROLES = frozenset({"student", "teacher", "flagger", "admin", "developer"})
DEFAULT_ROLES = ("student", "flagger")
ENTITLEMENTS = frozenset({"practice", "games", "tutor", "path", "library"})
DEFAULT_ENTITLEMENTS = ENTITLEMENTS - {"tutor"}
ROLE_PERMISSIONS = {
    "student": frozenset(),
    "teacher": frozenset({"exercises.use_all", "students.view", "games.use", "path.use", "exercises.use", "library.view"}),
    "flagger": frozenset({"flags.create"}),
    "admin": frozenset({"exercises.use_all", "students.view", "management.access", "users.manage",
                        "classes.manage", "content.manage", "flags.review", "games.use", "path.use",
                        "exercises.use", "library.view"}),
    "developer": frozenset({"*"}),
}
ENTITLEMENT_PERMISSIONS = {"practice": {"practice.use", "exercises.use"}, "games": {"games.use"},
                           "tutor": {"tutor.use"}, "path": {"path.use"}, "library": {"library.view"}}


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
    if "flagger" not in roles:
        try:
            if email in (a.ranked() or ()):
                roles.add("flagger")
        except AuthUnavailable:
            pass
    permissions = set().union(*(ROLE_PERMISSIONS[r] for r in roles))
    permissions.update(*[ENTITLEMENT_PERMISSIONS[e] for e in entitlements])
    return {**user, "token": token, "roles": sorted(roles), "entitlements": sorted(entitlements),
            "subject": str((assigned or {}).get("subject") or ""), "permissions": sorted(permissions),
            "teacher": bool(roles & {"teacher", "admin", "developer"}),
            "tutor": "flags.create" in permissions or "*" in permissions, "classes": mine, "teaches": teaches}


def has_permission(user: dict | None, permission: str) -> bool:
    permissions = set((user or {}).get("permissions", ()))
    return "*" in permissions or permission in permissions
