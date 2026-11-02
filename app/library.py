"""Shared digital-library validation and persistence operations."""
from __future__ import annotations

import logging
from typing import Callable

from .auth import AuthUnavailable, SupabaseAuth


MAX_TITLE = 80
log = logging.getLogger("ihki.library")

# The library's sections, in the order the teacher and the students see them. A section either holds videos itself or
# is split into sub-sections that do (מערכת דיגיטלית: one per level of the course, and נִתמרַّן). A video stands in one
# leaf section, kept in the `level` column of the `library` table; a video saved before there were sections has an
# empty `level` and stands in שלב א. A permission names a section or a sub-section: a section covers all of its own.
SECTIONS: list[dict] = [
    {"id": "digital", "name": "מערכת דיגיטלית", "children": [
        {"id": "d1", "name": "שלב א", "level": "1"},
        {"id": "d2", "name": "שלב ב", "level": "2"},
        {"id": "d3", "name": "שלב ג", "level": "3"},
        {"id": "d4", "name": "שלב ד", "level": "4"},
        {"id": "nitmaran", "name": "נִתמרַّן"},
    ]},
    {"id": "graduates", "name": "בוגרים מספרים"},
    {"id": "village", "name": "טיולים בכפר"},
    {"id": "avot", "name": "פרקי אבות עם אמיר"},
    {"id": "hacham", "name": "חַאחַ׳אם עידן"},
    {"id": "guides", "name": "מנחים ומתרגלים"},
]
LEGACY_SECTION = "d1"   # where a video from before the sections stands
ENTITLEMENT_PREFIX = "lib:"


def _walk(nodes: list[dict], parent: dict | None = None):
    for node in nodes:
        yield node, parent
        yield from _walk(node.get("children") or [], node)


NODES = {node["id"]: node for node, _ in _walk(SECTIONS)}
PARENT = {node["id"]: parent["id"] for node, parent in _walk(SECTIONS) if parent}
LEAVES = [node["id"] for node, _ in _walk(SECTIONS) if not node.get("children")]
SECTION_ENTITLEMENTS = [ENTITLEMENT_PREFIX + node_id for node_id in NODES]


def leaves_of(node_id: str) -> list[str]:
    """The leaf sections a section or sub-section covers."""
    node = NODES.get(node_id)
    if node is None:
        return []
    if not node.get("children"):
        return [node_id]
    return [leaf for child in node["children"] for leaf in leaves_of(child["id"])]


def label(section_id: str) -> str:
    """A section's full name: "מערכת דיגיטלית · שלב א"."""
    names, at = [], section_id
    while at:
        names.insert(0, NODES[at]["name"])
        at = PARENT.get(at, "")
    return " · ".join(names)


def section_of(video: dict) -> str:
    level = str(video.get("level") or "")
    return level if level in NODES and not NODES[level].get("children") else LEGACY_SECTION


def resolve_section(value: object) -> str:
    """A leaf section from its id or its name (with or without its parent's name); a LibraryError naming the choices
    when it is missing, unknown, or a section that is split into sub-sections."""
    text = " ".join(str(value or "").split())
    if not text:
        raise LibraryError("לאיזה מדור להעלות? " + choices())
    wanted = _plain(text)
    for node_id, node in NODES.items():
        if wanted in (node_id, _plain(node["name"]), _plain(label(node_id))):
            if node.get("children"):
                raise LibraryError(f"המדור {node['name']} מחולק לתתי-מדורים. בחרו אחד: "
                                   + ", ".join(NODES[c]["name"] for c in leaves_of(node_id)))
            return node_id
    raise LibraryError(f"אין מדור בשם {text}. " + choices())


def resolve_node(value: object) -> str:
    """Any section or sub-section from its id or its name (a section covers its sub-sections): what a class opens."""
    text = " ".join(str(value or "").split())
    wanted = _plain(text)
    for node_id, node in NODES.items():
        if text and wanted in (node_id, _plain(node["name"]), _plain(label(node_id))):
            return node_id
    raise LibraryError(f"אין מדור בשם {text}. " + choices())


def _plain(text: str) -> str:
    """A name as typed in a chat: without vowel points, shadda, gershayim or the separators between a section and its
    sub-section, so "נתמרן" and "מערכת דיגיטלית / שלב א" find their sections."""
    marks = {c: None for c in [*range(0x591, 0x5C8), *range(0x64B, 0x653), 0x5F3, 0x5F4, ord("'"), ord('"'), ord("׳")]}
    text = text.translate(marks)
    for sep in ("·", "/", ">", "-", ",", "|"):
        text = text.replace(sep, " ")
    return " ".join(text.split()).lower()


def choices() -> str:
    return "המדורים: " + ", ".join(label(leaf) for leaf in LEAVES)


def public_sections() -> list[dict]:
    """The section tree for a page or an MCP client: id, name, and the leaf ids each covers."""
    def one(node: dict) -> dict:
        out = {"id": node["id"], "name": node["name"], **({"level": node["level"]} if node.get("level") else {})}
        if node.get("children"):
            out["children"] = [one(child) for child in node["children"]]
        return out
    return [one(node) for node in SECTIONS]


def visible_sections(entitlements: set[str] | frozenset[str], everything: bool) -> list[str]:
    """The leaf sections a user may watch: every one for a teacher, else those their permissions name."""
    if everything:
        return list(LEAVES)
    mine = {leaf for e in entitlements if e.startswith(ENTITLEMENT_PREFIX) for leaf in leaves_of(e[len(ENTITLEMENT_PREFIX):])}
    return [leaf for leaf in LEAVES if leaf in mine]


class LibraryError(ValueError):
    """A safe, user-facing library operation error."""


class LibraryUnavailable(LibraryError):
    """The optional persistent content store cannot be used."""


def normalize_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if not title or len(title) > MAX_TITLE:
        raise LibraryError(f"לסרטון צריך שם (עד {MAX_TITLE} תווים)")
    return title


def normalize_url(value: object, parse: Callable[[str], str | None]) -> str:
    url = parse(str(value or ""))
    if not url:
        raise LibraryError("זה לא קישור לסרטון ב-Vimeo או ב-YouTube")
    return url


def rows(auth: SupabaseAuth | None, *, fresh: bool = False) -> list[dict]:
    if auth is None:
        raise LibraryUnavailable("ספריית הסרטונים אינה זמינה: Supabase אינו מוגדר")
    try:
        return auth.level_videos("library", fresh=fresh)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"ספריית הסרטונים אינה זמינה: {exc}") from exc


def ensure_unique(auth: SupabaseAuth | None, url: str, *, fresh: bool = True) -> None:
    same = next((video for video in rows(auth, fresh=fresh) if video.get("url") == url), None)
    if same is not None:
        raise LibraryError(f"הסרטון כבר נמצא בספרייה, במדור {label(section_of(same))}")


def add(auth: SupabaseAuth | None, title: str, url: str, *, section: str = LEGACY_SECTION,
        reject_duplicate: bool = True) -> None:
    if section not in LEAVES:
        raise LibraryError("לאיזה מדור להעלות? " + choices())
    if reject_duplicate:
        ensure_unique(auth, url, fresh=True)
    try:
        assert auth is not None
        auth.add_level_video("library", section, title, url)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן להוסיף לספרייה: {exc}") from exc


def rename(auth: SupabaseAuth | None, video_id: int, title: str) -> None:
    if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
        raise LibraryError("מזהה הסרטון אינו תקין")
    if all(video.get("id") != video_id for video in rows(auth, fresh=True)):
        raise LibraryError("הסרטון אינו נמצא בספרייה")
    try:
        assert auth is not None
        auth.rename_level_video("library", video_id, title)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן לשנות את שם הסרטון: {exc}") from exc


def move(auth: SupabaseAuth | None, video_id: int, section: str) -> None:
    """Moves a library video to another leaf section, keeping its title and link."""
    if section not in LEAVES:
        raise LibraryError("לאיזה מדור להעביר? " + choices())
    if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
        raise LibraryError("מזהה הסרטון אינו תקין")
    video = next((v for v in rows(auth, fresh=True) if v.get("id") == video_id), None)
    if video is None:
        raise LibraryError("הסרטון אינו נמצא בספרייה")
    try:
        assert auth is not None
        auth.update_level_video("library", video_id, level=section, title=video["title"], url=video["url"])
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן להעביר את הסרטון: {exc}") from exc


def audit(auth: SupabaseAuth | None, action: str, label: str, outcome: str, **details: object) -> None:
    """Best-effort audit using the existing management activity feed."""
    log.info("Grok library action=%s outcome=%s label=%s details=%s", action, outcome, label[:200], details)
    if auth is None or not hasattr(auth, "log_event"):
        return
    auth.log_event("integration", {"id": None, "email": "grok-library"}, label,
                   {"action": action, "outcome": outcome, **details})
