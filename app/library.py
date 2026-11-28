"""Shared digital-library validation and persistence operations."""
from __future__ import annotations

import logging
import secrets
from typing import Callable

from .auth import AuthUnavailable, ClassConflict, SupabaseAuth
from .phrases import LESSONS, LEVELS, on_serve


MAX_TITLE = 80
log = logging.getLogger("ihki.library")

# The library's sections, in the order the teacher and the students see them. A section either holds videos itself or
# is split into sub-sections that do (מערכת דיגיטלית: one per level of the course, and נִתמרַּן). A video stands in one
# section that can hold videos, kept in the `level` column of the `library` table; a video saved before there were
# sections has an empty `level` and stands in שלב א. A permission names a section or a sub-section: a section covers all
# of its own.
#
# This nested list is only the SEED. The sections live in the `library_sections` table (CREATE_LIBRARY_SECTIONS), which
# a manager extends with sub-sections (prepare_library_section / publish_library_section); `refresh` reads it into the
# tree every function below answers from. Until that table exists the seed is the whole tree, so nothing changes.
SEED_SECTIONS: list[dict] = [
    {"id": "digital", "name": "מערכת דיגיטלית", "children": [
        {"id": "d1", "name": "שלב א", "level": "1"},
        {"id": "d2", "name": "שלב ב", "level": "2"},
        {"id": "d3", "name": "שלב ג", "level": "3"},
        {"id": "d4", "name": "שלב ד", "level": "4"},
        {"id": "nitmaran", "name": "נִתמרַּן"},
    ]},
    {"id": "graduates", "name": "בוגרים מספרים"},
    {"id": "village", "name": "טיולים בכפר"},
    {"id": "avot", "name": "פרקי אבות עם אמיר"},
    {"id": "hacham", "name": "חַאחַ׳אם עידן"},
    {"id": "guides", "name": "מנחים ומתרגלים"},
]
LEGACY_SECTION = "d1"   # where a video from before the sections stands
ENTITLEMENT_PREFIX = "lib:"
MAX_DEPTH = 3           # digital library → section → sub-section → sub-sub-section: a section counts as one level
MAX_SECTION_NAME = 60


def _seed_rows(nodes: list[dict] = SEED_SECTIONS, parent: str | None = None) -> list[dict]:
    rows = []
    for at, node in enumerate(nodes):
        rows.append({"id": node["id"], "parent_id": parent, "name": node["name"], "ord": at,
                     "level": node.get("level") or "", "holds_videos": False})
        rows += _seed_rows(node.get("children") or [], node["id"])
    return rows


SEED_ROWS = _seed_rows()


def _lesson_rows() -> list[dict]:
    """A sub-section for each lesson of each level of the course, under the level's own section (שלב א ‣ שיעור 1, שלב ב ‣
    שיעור 10…), in the lesson's order. They are built from the course itself and always part of the tree, like the seed,
    but they are not in SEED_ROWS: they have no permission of their own. Whoever opens a level (a class of שלב א, an
    account with its permission) sees all of its lessons' sections, and so a student sees the lessons of their level."""
    parents = {level["id"]: "d" + level["id"] for level in LEVELS}
    rows = []
    for at, lesson in enumerate(LESSONS):
        parent = parents.get(lesson["level"])
        if parent:
            rows.append({"id": "lesson-" + lesson["id"].replace(".", "-"), "parent_id": parent, "name": lesson["name"],
                         "ord": at, "level": "", "holds_videos": False})
    return rows


LESSON_ROWS = _lesson_rows()
BASE_ROWS = [*SEED_ROWS, *LESSON_ROWS]   # the tree before the table adds to it
# Permissions ("lib:<id>") and a class's sections exist only for the seeded sections: the permissions table's check
# constraint lists them (see main.ENTITLEMENTS_SQL). A sub-section added later has none of its own: whoever has its
# parent has it.
SEED_IDS = frozenset(row["id"] for row in SEED_ROWS)
SECTION_ENTITLEMENTS = [ENTITLEMENT_PREFIX + row["id"] for row in SEED_ROWS]
# the sections whose own permission an account's exception may name (see access.clean_overrides): what could hold
# videos when there were no sub-sections
SEED_HOLDERS = frozenset(row["id"] for row in SEED_ROWS
                         if not any(other["parent_id"] == row["id"] for other in SEED_ROWS))

CREATE_LIBRARY_SECTIONS = (
    "create table public.library_sections (id text primary key, "
    "parent_id text references public.library_sections(id), name text not null check (length(btrim(name)) > 0), "
    "ord integer not null default 0, level text not null default '', holds_videos boolean not null default false, "
    "deleted boolean not null default false, "
    "created_by text, created_at timestamptz not null default now()); "
    "create unique index library_sections_sibling_name on public.library_sections "
    "(coalesce(parent_id, ''), lower(btrim(name))) where not deleted; "
    "alter table public.library_sections enable row level security; "
    "insert into public.library_sections (id, parent_id, name, ord, level) values "
    + ", ".join("('{id}', {parent}, '{name}', {ord}, '{level}')".format(
        id=r["id"], parent=f"'{r['parent_id']}'" if r["parent_id"] else "null",
        name=r["name"].replace("'", "''"), ord=r["ord"], level=r["level"]) for r in SEED_ROWS)
    + "; notify pgrst, 'reload schema';")

# for a table made before sections could be deleted (a deleted section's row stays, so its id is never reused)
MIGRATE_LIBRARY_SECTIONS = (
    "alter table public.library_sections add column if not exists deleted boolean not null default false; "
    "drop index if exists public.library_sections_sibling_name; "
    "create unique index library_sections_sibling_name on public.library_sections "
    "(coalesce(parent_id, ''), lower(btrim(name))) where not deleted; notify pgrst, 'reload schema';")


class LibraryError(ValueError):
    """A safe, user-facing library operation error."""


class LibraryUnavailable(LibraryError):
    """The optional persistent content store cannot be used."""


class Tree:
    """The sections as rows give them: `nodes` (id → {id, name, parent, children, level, holds}), `roots`, in order.
    `holds` is true where a video can stand: a section without sub-sections, or a parent that kept its own videos."""

    def __init__(self, rows: list[dict]):
        self.rows = [dict(row) for row in rows]   # every stored row, a deleted one too (its id stays taken)
        by_parent: dict[str, list[dict]] = {}
        for row in self.rows:
            if row.get("deleted"):
                continue
            by_parent.setdefault(row.get("parent_id") or "", []).append(row)
        for siblings in by_parent.values():
            siblings.sort(key=lambda r: (r.get("ord") or 0, r["id"]))
        self.nodes: dict[str, dict] = {}
        self.roots: list[str] = []

        def grow(row: dict, parent: str, depth: int) -> None:
            if row["id"] in self.nodes or depth > MAX_DEPTH:   # a loop or a row deeper than allowed is not shown
                return
            kids = by_parent.get(row["id"], [])
            self.nodes[row["id"]] = {"id": row["id"], "name": row["name"], "parent": parent, "depth": depth,
                                     "level": row.get("level") or "", "children": [], "own": bool(row.get("holds_videos"))}
            for kid in kids:
                grow(kid, row["id"], depth + 1)
            self.nodes[row["id"]]["children"] = [k["id"] for k in kids if k["id"] in self.nodes]
            self.nodes[row["id"]]["holds"] = True   # a video can stand in any section, whether or not it has sub-sections

        for row in by_parent.get("", []):
            grow(row, "", 1)
            if row["id"] in self.nodes:
                self.roots.append(row["id"])

    def walk(self, ids: list[str] | None = None):
        for node_id in self.roots if ids is None else ids:
            yield self.nodes[node_id]
            yield from self.walk(self.nodes[node_id]["children"])

    def holders(self) -> list[str]:
        return [node["id"] for node in self.walk() if node["holds"]]

    def subtree(self, node_id: str) -> list[str]:
        """The section and every section below it."""
        node = self.nodes.get(node_id)
        return [] if node is None else [n["id"] for n in [node, *self.walk(node["children"])]]

    def covered(self, node_id: str) -> list[str]:
        return [i for i in self.subtree(node_id) if self.nodes[i]["holds"]]

    def height(self, node_id: str) -> int:
        """How many levels the section and what is below it take: 1 for a section without sub-sections."""
        return 1 + max((self.height(c) for c in self.nodes[node_id]["children"]), default=0)

    def label(self, section_id: str) -> str:
        names, at = [], section_id
        while at:
            names.insert(0, self.nodes[at]["name"])
            at = self.nodes[at]["parent"]
        return " · ".join(names)

    def section_of(self, video: dict) -> str:
        level = str(video.get("level") or "")
        node = self.nodes.get(level)
        if node and node["holds"]:
            return level
        legacy = self.nodes.get(LEGACY_SECTION)
        return LEGACY_SECTION if legacy and legacy["holds"] else (self.holders() or [LEGACY_SECTION])[0]

    def visible(self, entitlements) -> list[str]:
        """The sections that can hold videos which the permissions ("lib:<id>") cover."""
        mine = {leaf for e in entitlements if e.startswith(ENTITLEMENT_PREFIX) for leaf in self.covered(e[len(ENTITLEMENT_PREFIX):])}
        return [leaf for leaf in self.holders() if leaf in mine]


_tree = Tree(BASE_ROWS)
_stored: list[dict] = []   # the table's rows as last read (empty: the seed is the tree)


def _rebuild_lessons() -> None:   # another version of the course is served (phrases.serve): its lessons' sections
    global _tree, LESSON_ROWS, BASE_ROWS
    with_lessons = BASE_ROWS is not SEED_ROWS   # (a test may leave the lessons' sections out)
    LESSON_ROWS = _lesson_rows()
    if with_lessons:
        BASE_ROWS = [*SEED_ROWS, *LESSON_ROWS]
    _tree = Tree([*_stored, *_missing(_stored)]) if _stored else Tree(BASE_ROWS)


on_serve(_rebuild_lessons)


def tree() -> Tree:
    return _tree


def reset() -> None:
    """Back to the seed (what a deployment without the table has)."""
    global _tree, _stored
    _stored = []
    _tree = Tree(BASE_ROWS)


def leaf_sections() -> list[str]:
    """The sections without sub-sections."""
    return [i for i in _tree.holders() if not _tree.nodes[i]["children"]]


def holders() -> list[str]:
    """The sections a video can stand in, in order: those without sub-sections, and parents that kept their videos."""
    return _tree.holders()


def __getattr__(name: str):   # the old names, for code that has not moved to the functions
    if name == "LEAVES":
        return holders()
    if name == "NODES":
        return _tree.nodes
    raise AttributeError(name)


def has_node(node_id: str) -> bool:
    return node_id in _tree.nodes


def depth(node_id: str) -> int:
    return _tree.nodes[node_id]["depth"]


def covered(node_id: str) -> list[str]:
    """The sections that can hold videos which a permission on `node_id` covers: it (if it holds videos) and all below."""
    return _tree.covered(node_id)


leaves_of = covered   # the old name


def label(section_id: str) -> str:
    """A section's full name: "מערכת דיגיטלית · שלב א"."""
    return _tree.label(section_id)


def section_of(video: dict) -> str:
    return _tree.section_of(video)


def _find(text: str) -> list[str]:
    """The sections a typed id or name means: by id, else by full name, else by its name or the end of its path ("שלב ב /
    חזרה"), which two sub-sections of different sections can share."""
    wanted = _plain(text)
    if not wanted:
        return []
    ends = lambda i: (" " + _plain(label(i))).endswith(" " + wanted)   # the name, or its last words of the path
    for same in (lambda i, n: wanted == i, lambda i, n: wanted == _plain(label(i)), lambda i, n: ends(i)):
        found = [i for i, n in _tree.nodes.items() if same(i, n)]
        if found:
            return found
    return []


def resolve_node(value: object) -> str:
    """Any section or sub-section from its id or its name (a section covers its sub-sections): what a class opens, or
    where a sub-section is added."""
    text = " ".join(str(value or "").split())
    found = _find(text)
    if len(found) > 1:
        raise LibraryError(f"השם {text} מופיע ביותר ממדור אחד: " + ", ".join(f"{label(i)} ({i})" for i in found)
                           + ". ציינו את המזהה או את השם המלא.")
    if not found:
        raise LibraryError(f"אין מדור בשם {text}. " + choices())
    return found[0]


def resolve_section(value: object) -> str:
    """A section a video can stand in (any) from its id or its name (with or without its parent's name); a LibraryError
    naming the choices when it is missing or unknown."""
    text = " ".join(str(value or "").split())
    if not text:
        raise LibraryError("לאיזה מדור להעלות? " + choices())
    node_id = resolve_node(text)
    return node_id


def _plain(text: str) -> str:
    """A name as typed in a chat: without vowel points, shadda, gershayim or the separators between a section and its
    sub-section, so "נתמרן" and "מערכת דיגיטלית / שלב א" find their sections."""
    marks = {c: None for c in [*range(0x591, 0x5C8), *range(0x64B, 0x653), 0x5F3, 0x5F4, ord("'"), ord('"'), ord("׳")]}
    text = text.translate(marks)
    for sep in ("·", "/", ">", "-", ",", "|"):
        text = text.replace(sep, " ")
    return " ".join(text.split()).lower()


def choices() -> str:
    return "המדורים: " + ", ".join(label(leaf) for leaf in holders())


def upload_targets() -> list[dict]:
    """Where a video can go, every section: {id, name (the full path), has_sub_sections}."""
    return [{"id": i, "name": label(i), **({"has_sub_sections": True} if _tree.nodes[i]["children"] else {})}
            for i in holders()]


def public_sections() -> list[dict]:
    """The section tree for a page or an MCP client: id, name, whether videos stand in it (`holds_videos`), and its
    sub-sections."""
    def one(node: dict) -> dict:
        out = {"id": node["id"], "name": node["name"], "depth": node["depth"], "holds_videos": node["holds"],
               **({"level": node["level"]} if node["level"] else {})}
        if node["children"]:
            out["children"] = [one(_tree.nodes[child]) for child in node["children"]]
        return out
    return [one(_tree.nodes[root]) for root in _tree.roots]


def visible_sections(entitlements: set[str] | frozenset[str], everything: bool) -> list[str]:
    """The sections a user may watch: every one for a teacher, else those their permissions name or sit under."""
    return holders() if everything else _tree.visible(entitlements)


def refresh(auth, *, fresh: bool = False, strict: bool = False) -> None:
    """Reads the sections from the `library_sections` table into the tree. Without the table (or a store that has
    none) the tree is the seed. When the store cannot be reached the tree stays as it was; `strict` raises instead, for
    an operation that must not act on a guess (a sub-section's videos would otherwise be taken for שלב א's)."""
    global _tree
    getter = getattr(auth, "library_sections", None)
    if getter is None:
        reset()
        return
    try:
        stored = getter(fresh)
    except AuthUnavailable as exc:
        if strict:
            raise LibraryUnavailable(f"מדורי הספרייה אינם זמינים: {exc}") from exc
        log.warning("library sections not read: %s", exc)
        return
    if not stored:
        reset()
        return
    global _stored
    _stored = list(stored)
    _tree = Tree([*stored, *_missing(stored)])   # a seeded id is never missing


def _missing(stored: list[dict]) -> list[dict]:
    """The built-in rows the table lacks. A lesson's section is left out where a manager already made a sub-section of
    that name under the same level (their "שיעור 1" is the lesson's section; a second one would stand beside it)."""
    known = {row["id"] for row in stored}
    names = {(row.get("parent_id") or "", _plain(row["name"])) for row in stored if not row.get("deleted")}
    lessons = {row["id"] for row in LESSON_ROWS}
    return [row for row in BASE_ROWS if row["id"] not in known
            and not (row["id"] in lessons and (row["parent_id"], _plain(row["name"])) in names)]


def lesson_sections() -> dict[str, set[str]]:
    """The lessons' sections that are in the tree, by the level of the course: each lesson's own, or the sub-section a
    manager named after it under the same level."""
    out: dict[str, set[str]] = {}
    for row in LESSON_ROWS:
        parent = _tree.nodes.get(row["parent_id"])
        if parent is None:
            continue
        ids = [row["id"]] if row["id"] in _tree.nodes else [
            kid for kid in parent["children"] if _plain(_tree.nodes[kid]["name"]) == _plain(row["name"])]
        out.setdefault(parent["level"], set()).update(ids)
    return out


def lesson_section(lesson: str) -> str | None:
    """The section of one lesson of the course (its id, "2" or "2.1"): the lesson's own, or the sub-section a manager
    named after it under the same level ("שיעור 2" of שלב א), or else the level's section. None for a lesson the
    course does not have, or whose level has no section."""
    row = next((r for r in LESSON_ROWS if r["id"] == "lesson-" + str(lesson).replace(".", "-")), None)
    parent = _tree.nodes.get(row["parent_id"]) if row else None
    if parent is None:
        return None
    if row["id"] in _tree.nodes:
        return row["id"]
    return next((kid for kid in parent["children"] if _plain(_tree.nodes[kid]["name"]) == _plain(row["name"])), parent["id"])


def materialize(auth) -> None:
    """Writes the built-in sections the table lacks (the seeded ones, and a section for each lesson) as rows, so a manager
    can rename, move or delete them like any other; ids already there, a deleted one too, are kept as they are."""
    missing = _missing(auth.library_sections(True) or [])
    if missing:
        auth.seed_library_sections(missing)


def normalize_section_name(value: object) -> str:
    name = " ".join(str(value or "").split())
    if not name or len(name) > MAX_SECTION_NAME:
        raise LibraryError(f"למדור צריך שם (עד {MAX_SECTION_NAME} תווים)")
    if any(sep in name for sep in "·/>|"):
        raise LibraryError("בשם המדור אי אפשר להשתמש בתווים · / > |")
    return name


def new_section_id() -> str:
    return "s" + secrets.token_hex(5)


def plan_section(parent: object, name: object, videos: list[dict]) -> dict:
    """What adding a sub-section would do, or a LibraryError: the parent (an id or a name), the new Hebrew name.
    `videos` are the library's rows now, to say what happens to the ones the parent holds."""
    parent_id = resolve_node(parent)
    clean = normalize_section_name(name)
    node = _tree.nodes[parent_id]
    if node["depth"] >= MAX_DEPTH:
        raise LibraryError(f"אי אפשר להוסיף תת-מדור תחת {label(parent_id)}: המדורים מגיעים לעד {MAX_DEPTH} רמות")
    wanted = _plain(clean)
    for sibling in node["children"]:
        if _plain(_tree.nodes[sibling]["name"]) == wanted:
            raise LibraryError(f"כבר יש תת-מדור בשם {clean} תחת {label(parent_id)}")
    if wanted in _tree.nodes:
        raise LibraryError("השם הזה זהה למזהה של מדור קיים. בחרו שם אחר")
    held = sum(1 for video in videos if node["holds"] and section_of(video) == parent_id)
    return {"operation": "create_library_section", "parent": parent_id, "parent_label": label(parent_id),
            "name": clean, "new_id": new_section_id(), "new_label": label(parent_id) + " · " + clean,
            "level": node["depth"] + 1, "siblings": list(node["children"]),
            "existing_videos": held}


def create_section(auth, plan: dict, by: str) -> dict:
    """Adds the sub-section a plan describes, if the sections and the parent's videos are still as the plan saw them."""
    if auth is None or not hasattr(auth, "add_library_section"):
        raise LibraryUnavailable("הוספת תתי-מדורים אינה זמינה: " + _missing_table())
    refresh(auth, fresh=True, strict=True)
    if auth.library_sections(True) is None:
        raise LibraryUnavailable(_missing_table())
    changed = LibraryError("המדורים או הסרטונים שלהם השתנו מאז ההכנה. הכינו את התת-מדור מחדש.")
    if plan["parent"] not in _tree.nodes:
        raise changed
    fresh_plan = plan_section(plan["parent"], plan["name"], rows(auth, fresh=True))   # also the name and depth rules
    if fresh_plan["siblings"] != plan["siblings"]:
        raise changed
    parent = _tree.nodes[plan["parent"]]
    try:
        materialize(auth)
        auth.add_library_section({"id": plan["new_id"], "parent_id": plan["parent"], "name": plan["name"],
                                  "ord": len(parent["children"]),   # after its siblings (their ord runs 0, 1, …)
                                  "level": "", "holds_videos": False, "created_by": by})
    except ClassConflict as exc:
        raise LibraryError(f"כבר יש תת-מדור בשם {plan['name']} תחת {plan['parent_label']}") from exc
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן להוסיף את התת-מדור: {exc}") from exc
    refresh(auth, fresh=True, strict=True)
    return {"section": plan["new_id"], "section_name": label(plan["new_id"])}


def _missing_table() -> str:
    return ("טבלת library_sections לא קיימת. הריצו ב-SQL Editor של Supabase: " + CREATE_LIBRARY_SECTIONS)


def normalize_title(value: object) -> str:
    title = " ".join(str(value or "").split())
    if not title or len(title) > MAX_TITLE:
        raise LibraryError(f"לסרטון צריך שם (עד {MAX_TITLE} תווים)")
    return title


NOT_A_LINK = "זה לא קישור לסרטון ב-Vimeo או ב-YouTube, או לקובץ PDF"
# where a document is kept online: a file whose address ends in .pdf anywhere, or a shared file of these services
DOC_HOSTS = ("drive.google.com", "docs.google.com", "dropbox.com", "onedrive.live.com", "1drv.ms", "sharepoint.com")


def doc_link(text: str) -> str | None:
    """A link to a document (a worksheet, a song's words) as it is kept: the https address as it was given. None when
    it is not one: neither a .pdf file nor a shared file of Google Drive, Dropbox or OneDrive."""
    import urllib.parse as up
    url = str(text or "").strip()
    try:
        parts = up.urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or len(url) > 1000 or any(c.isspace() for c in url):
        return None
    if parts.path.lower().endswith(".pdf") or any(host == h or host.endswith("." + h) for h in DOC_HOSTS):
        return url
    return None


def normalize_url(value: object, parse: Callable[[str], str | None]) -> str:
    """A video's link (`parse`: YouTube or Vimeo, as kept), else a document's (see `doc_link`)."""
    url = parse(str(value or "")) or doc_link(str(value or ""))
    if not url:
        raise LibraryError(NOT_A_LINK)
    return url


def rows(auth: SupabaseAuth | None, *, fresh: bool = False) -> list[dict]:
    if auth is None:
        raise LibraryUnavailable("ספריית הסרטונים אינה זמינה: Supabase אינו מוגדר")
    refresh(auth, fresh=fresh, strict=True)   # which section a video stands in depends on the sections
    try:
        return auth.level_videos("library", fresh=fresh)
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"ספריית הסרטונים אינה זמינה: {exc}") from exc


def ensure_unique(auth: SupabaseAuth | None, url: str, *, section: str | None = None, fresh: bool = True) -> None:
    """A video may stand in several sections (a course's video beside the level's): only twice in one section is
    refused. Without a section, anywhere in the library."""
    same = next((video for video in rows(auth, fresh=fresh) if video.get("url") == url
                 and (section is None or section_of(video) == section)), None)
    if same is not None:
        raise LibraryError(f"הסרטון כבר נמצא במדור {label(section_of(same))}")


def add(auth: SupabaseAuth | None, title: str, url: str, *, section: str = LEGACY_SECTION,
        reject_duplicate: bool = True) -> None:
    if auth is not None:
        refresh(auth, fresh=True, strict=True)   # a section may have been split since the page was drawn
    if section not in holders():
        raise LibraryError("לאיזה מדור להעלות? " + choices())
    if reject_duplicate:
        ensure_unique(auth, url, section=section, fresh=True)
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


def move(auth: SupabaseAuth | None, video_id: int, section: str, *, last: bool = False) -> None:
    """Moves a library video to another section that holds videos, keeping its title and link; `last` puts it after
    the destination's other videos (otherwise it keeps the place its `ord` gives it)."""
    if auth is not None:
        refresh(auth, fresh=True, strict=True)
    if section not in holders():
        raise LibraryError("לאיזה מדור להעביר? " + choices())
    if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
        raise LibraryError("מזהה הסרטון אינו תקין")
    video = next((v for v in rows(auth, fresh=True) if v.get("id") == video_id), None)
    if video is None:
        raise LibraryError("הסרטון אינו נמצא בספרייה")
    try:
        assert auth is not None
        auth.update_level_video("library", video_id, level=section, title=video["title"], url=video["url"])
        if last and hasattr(auth, "order_level_videos"):
            there = [v["id"] for v in rows(auth, fresh=True) if section_of(v) == section and v["id"] != video_id]
            auth.order_level_videos("library", [*there, video_id])
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"לא ניתן להעביר את הסרטון: {exc}") from exc


def audit(auth: SupabaseAuth | None, action: str, label: str, outcome: str, **details: object) -> None:
    """Best-effort audit using the existing management activity feed."""
    log.info("Grok library action=%s outcome=%s label=%s details=%s", action, outcome, label[:200], details)
    if auth is None or not hasattr(auth, "log_event"):
        return
    auth.log_event("integration", {"id": None, "email": "grok-library"}, label,
                   {"action": action, "outcome": outcome, **details})
