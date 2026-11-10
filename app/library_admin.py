"""Editing the digital library after it is made: renaming a section, moving one under another parent, deleting an empty
one, and moving a video to another section.

Each operation is a plan and an application. A plan reads the sections, the videos and the classes, changes nothing, and
returns an exact preview (full path before and after, what is carried along, who gains or loses access) with a `state`:
what the plan looked at. Applying a plan builds the plan again from the arguments it kept; if its `state` is no longer
the same, nothing is written.

Access, as the server already has it (library.Tree.visible): a permission "lib:<id>" exists only for the seeded sections
and covers the section and everything below it, wherever it stands now. A sub-section added later has none of its own and
is covered through its ancestors. So a section keeps its own permission through a rename or a move, and gains or loses
the permissions of the parents it moves under or away from. Nothing here adds a rule."""
from __future__ import annotations

from typing import Any

from . import library, teacher_operations
from .access import TIER_NAMES, TIERS, class_sections, effective_features, feature_table
from .auth import AuthUnavailable, ClassConflict
from .library import ENTITLEMENT_PREFIX, LibraryError, LibraryUnavailable, Tree, MAX_DEPTH

OPERATIONS = ("rename_library_section", "move_library_section", "delete_library_section", "move_library_video")


def _context(auth) -> tuple[Tree, list[dict], list[dict]]:
    """The sections, the videos and the classes as they are now (a LibraryError when the sections cannot be edited)."""
    if auth is None or not hasattr(auth, "update_library_section"):
        raise LibraryUnavailable("עריכת מדורים אינה זמינה: " + library._missing_table())
    library.refresh(auth, fresh=True, strict=True)
    try:
        if auth.library_sections(True) is None:
            raise LibraryUnavailable(library._missing_table())
        videos = library.rows(auth, fresh=True)
        classes = auth.classes(True) if hasattr(auth, "classes") else []
    except AuthUnavailable as exc:
        raise LibraryUnavailable(f"מדורי הספרייה אינם זמינים: {exc}") from exc
    return library.tree(), videos, classes


def _edit(tree: Tree, changes: dict[str, dict]) -> Tree:
    """The tree as it would be with these changes to rows ({id: fields})."""
    return Tree([{**row, **changes.get(row["id"], {})} for row in tree.rows])


def _siblings(tree: Tree, parent: str) -> list[str]:
    return list(tree.nodes[parent]["children"]) if parent else list(tree.roots)


def _around(tree: Tree, parent: str) -> list[str]:
    """A section's parent and its siblings' parent's children: whose names and places a plan depends on."""
    return ([parent] if parent else []) + _siblings(tree, parent)


def _need_deleted_column(auth) -> None:
    rows = auth.library_sections(True) or []
    if rows and "deleted" not in rows[0]:
        raise LibraryUnavailable("כדי למחוק מדורים צריך להוסיף עמודה לטבלה. הריצו ב-SQL Editor של Supabase: "
                                 + library.MIGRATE_LIBRARY_SECTIONS)


def _name_taken(tree: Tree, parent: str, name: str, *, besides: str = "") -> bool:
    wanted = library._plain(name)
    return any(library._plain(tree.nodes[s]["name"]) == wanted for s in _siblings(tree, parent) if s != besides)


def _parent_effect(tree: Tree, videos: list[dict], parent: str) -> dict[str, Any]:
    """What a first sub-section does to a parent that has none: it keeps its videos (and stays a place to upload to), or,
    with none, becomes a container."""
    node = tree.nodes[parent]
    first = not node["children"]
    held = sum(1 for v in videos if node["holds"] and tree.section_of(v) == parent)
    return {"keeps": bool(first and held) or bool(node["own"]), "container": bool(first and not held and not node["own"]),
            "held": held, "first": first}


def _tiers(auth) -> list[str]:
    """The tiers that see every section whatever a class opens (the feature library.all_sections): a change here does
    not touch them."""
    try:
        state = effective_features(feature_table(auth))
        return [TIER_NAMES[t] for t in TIERS if state[t]["library.all_sections"]["enabled"]]
    except Exception:   # the feature table is optional; the preview then says nothing about tiers
        return []


def _access(auth, before: Tree, after: Tree, classes: list[dict], watch: list[str], videos: list[dict]) -> dict[str, Any]:
    """Who gains or loses which sections: each class's view through its sections, before and after; the accounts whose own
    exception names one of `watch`; and the tiers that see everything."""
    changed = []
    with_videos = {before.section_of(v) for v in videos}
    # an empty section that only stops (or starts) being a place to upload to - it got its first sub-section, lost its
    # last one, or was deleted - gives nobody anything to watch: not an access change
    flipped = {i for i in set(before.holders()) | set(after.holders())
               if i not in with_videos and (i not in before.nodes or i not in after.nodes
                                            or before.nodes[i]["holds"] != after.nodes[i]["holds"])}
    for c in classes:
        entitlements = {ENTITLEMENT_PREFIX + s for s in class_sections(c)}
        was, now = set(before.visible(entitlements)) - flipped, set(after.visible(entitlements)) - flipped
        gains = [after.label(i) for i in after.holders() if i in now - was]
        loses = [before.label(i) for i in before.holders() if i in was - now]
        if gains or loses:
            changed.append({"id": c.get("id"), "name": c.get("name"), "students": len(c.get("emails") or []),
                            "gains": gains, "loses": loses})
    own = 0
    try:
        for row in (auth.access_rows() or []) if hasattr(auth, "access_rows") else []:
            if any(k.startswith(ENTITLEMENT_PREFIX) and k[len(ENTITLEMENT_PREFIX):] in watch
                   for k in (row.get("overrides") or {})):
                own += 1
    except AuthUnavailable:
        own = 0
    return {"classes": changed, "accounts_with_own_exception": own, "tiers_that_see_every_section": _tiers(auth),
            "rule": "הרשאה למדור חלה גם על כל תתי-המדורים שלו. הרשאה נשארת צמודה למזהה של המדור, ולכן שינוי שם או "
                    "העברה לא מוחקים אותה, אבל מדור שעבר מקבל גם את ההרשאות של ההורה החדש ומאבד את אלה של הישן."}


def _fingerprint(tree: Tree, videos: list[dict], sections: list[str], video_sections: list[str]) -> dict[str, Any]:
    """What a plan looked at: these sections' rows (name, parent, sub-sections, whether they hold videos) and the videos
    that stand in `video_sections`."""
    rows = [(i, tree.nodes[i]["name"], tree.nodes[i]["parent"], tuple(tree.nodes[i]["children"]), tree.nodes[i]["holds"],
             tree.nodes[i]["own"]) if i in tree.nodes else (i, None) for i in dict.fromkeys(sections)]
    held = sorted((v["id"], tree.section_of(v), v.get("title"), v.get("url")) for v in videos
                  if tree.section_of(v) in set(video_sections))
    return {"sections": rows, "videos": held}


def _finish(auth, op: str, tree: Tree, after: Tree, videos: list[dict], classes: list[dict], sid: str, args: dict,
            watch: list[str], extra: dict[str, Any]) -> dict[str, Any]:
    subtree = tree.subtree(sid)
    impact = _access(auth, tree, after, classes, subtree, videos)
    plan = {"operation": op, "args": args, "section": sid, "before": tree.label(sid),
            "after": after.label(sid) if sid in after.nodes else None,
            "videos_affected": sum(1 for v in videos if tree.section_of(v) in subtree),
            "sub_sections_affected": len(subtree) - 1, "sub_sections": [tree.label(i) for i in subtree[1:]],
            "access": impact, **extra}
    plan["state"] = {**_fingerprint(tree, videos, [*subtree, *watch], subtree), "classes": impact["classes"]}
    return plan


def public(plan: dict) -> dict:
    """The preview a manager is shown: the plan without what only the server needs."""
    return {k: v for k, v in plan.items() if k not in ("args", "state")}


def plan_rename(auth, section: object, name: object) -> dict:
    tree, videos, classes = _context(auth)
    sid = library.resolve_node(section)
    clean = library.normalize_section_name(name)
    node = tree.nodes[sid]
    if clean == node["name"]:
        raise LibraryError(f"זה כבר שם המדור {tree.label(sid)}")
    if _name_taken(tree, node["parent"], clean, besides=sid):
        raise LibraryError(f"כבר יש מדור בשם {clean} באותו מקום, תחת "
                           + (tree.label(node["parent"]) if node["parent"] else "הרמה העליונה"))
    if library._plain(clean) in tree.nodes:
        raise LibraryError("השם הזה זהה למזהה של מדור קיים. בחרו שם אחר")
    after = _edit(tree, {sid: {"name": clean}})
    return _finish(auth, "rename_library_section", tree, after, videos, classes, sid,
                   {"section": sid, "name": clean}, _around(tree, node["parent"]),
                   {"id": sid, "new_name": clean, "id_unchanged": True})


def plan_move_section(auth, section: object, new_parent: object) -> dict:
    tree, videos, classes = _context(auth)
    sid = library.resolve_node(section)
    top = not str(new_parent or "").strip()
    pid = "" if top else library.resolve_node(new_parent)
    node = tree.nodes[sid]
    if pid == node["parent"]:
        raise LibraryError(f"{tree.label(sid)} כבר נמצא שם")
    if pid and pid in tree.subtree(sid):
        raise LibraryError("אי אפשר להעביר מדור לתוך עצמו או לתוך אחד מתתי-המדורים שלו")
    depth = 1 if top else tree.nodes[pid]["depth"] + 1
    if depth + tree.height(sid) - 1 > MAX_DEPTH:
        raise LibraryError(f"ההעברה תגרום ליותר מ-{MAX_DEPTH} רמות: {tree.label(sid)} כולל תתי-המדורים שלו "
                           f"לוקח {tree.height(sid)} רמות, ומתחת ל{tree.label(pid) if pid else 'הרמה העליונה'} "
                           f"נשארות {MAX_DEPTH - depth + 1}")
    if _name_taken(tree, pid, node["name"]):
        raise LibraryError(f"כבר יש מדור בשם {node['name']} תחת {tree.label(pid) if pid else 'הרמה העליונה'}")
    changes: dict[str, dict] = {sid: {"parent_id": pid or None, "ord": len(_siblings(tree, pid))}}
    extra: dict[str, Any] = {"id": sid, "id_unchanged": True, "new_parent": pid or None,
                             "new_parent_name": tree.label(pid) if pid else "הרמה העליונה",
                             "old_parent": node["parent"] or None,
                             "old_parent_name": tree.label(node["parent"]) if node["parent"] else "הרמה העליונה",
                             "new_level": depth, "carries_videos_and_sub_sections": True}
    effects = []
    if pid:
        effect = _parent_effect(tree, videos, pid)
        extra["keep_parent_flag"] = effect["keeps"] and not tree.nodes[pid]["own"]
        if effect["keeps"] and effect["first"]:
            effects.append(f"ב{tree.label(pid)} יש {effect['held']} סרטונים והם נשארים בו; הוא נשאר מדור שאפשר להעלות אליו")
            changes[pid] = {"holds_videos": True}
        elif effect["container"]:
            effects.append(f"{tree.label(pid)} ריק מסרטונים, ולכן יהפוך למדור-אב: סרטונים יועלו רק לתתי-המדורים שלו")
    old = node["parent"]
    if old and len(tree.nodes[old]["children"]) == 1 and not tree.nodes[old]["holds"]:
        effects.append(f"{tree.label(old)} יישאר בלי תתי-מדורים ויהפוך למדור שאפשר להעלות אליו סרטונים")
    extra["effects_on_parents"] = effects
    after = _edit(tree, changes)
    watch = [x for x in (old, *_around(tree, pid)) if x]
    return _finish(auth, "move_library_section", tree, after, videos, classes, sid,
                   {"section": sid, "new_parent": pid}, watch, extra)


def plan_delete(auth, section: object) -> dict:
    tree, videos, classes = _context(auth)
    sid = library.resolve_node(section)
    node = tree.nodes[sid]
    held = [v for v in videos if tree.section_of(v) == sid or str(v.get("level") or "") == sid]
    if held or node["children"]:
        still = []
        if held:
            still.append(f"{len(held)} סרטונים")
        if node["children"]:
            still.append(f"{len(node['children'])} תתי-מדורים (" + ", ".join(tree.nodes[c]["name"] for c in node["children"]) + ")")
        raise LibraryError(f"אי אפשר למחוק את {tree.label(sid)}: עדיין יש בו " + " ו-".join(still)
                           + ". מוחקים רק מדור ריק. העבירו את הסרטונים והסירו את תתי-המדורים קודם.")
    _need_deleted_column(auth)
    after = _edit(tree, {sid: {"deleted": True}})
    assigned = [{"id": c.get("id"), "name": c.get("name"), "students": len(c.get("emails") or [])}
                for c in classes if sid in (c.get("library_sections") or [])]
    parent = node["parent"]
    effects = []
    if parent and len(tree.nodes[parent]["children"]) == 1 and not tree.nodes[parent]["holds"]:
        effects.append(f"{tree.label(parent)} יישאר בלי תתי-מדורים ויהפוך למדור שאפשר להעלות אליו סרטונים")
    plan = _finish(auth, "delete_library_section", tree, after, videos, classes, sid, {"section": sid},
                   _around(tree, parent),
                   {"id": sid, "id_is_retired": True, "removed_from_class_assignments": assigned,
                    "effects_on_parent": effects,
                    "removed_from_feature_access": "אין: הגדרות הגישה לפי שכבה אינן לפי מדור (רק library.all_sections, לכל המדורים)"})
    plan["state"]["assigned"] = assigned
    return plan


def plan_move_video(auth, video_id: object, destination: object) -> dict:
    tree, videos, classes = _context(auth)
    if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
        raise LibraryError("מזהה הסרטון אינו תקין")
    video = next((v for v in videos if v.get("id") == video_id), None)
    if video is None:
        raise LibraryError("הסרטון אינו נמצא בספרייה")
    dest = library.resolve_section(destination)   # only a section that holds videos
    src = tree.section_of(video)
    if dest == src:
        raise LibraryError(f"הסרטון כבר במדור {tree.label(dest)}")
    changed = []
    for c in classes:
        sees = set(tree.visible({ENTITLEMENT_PREFIX + s for s in class_sections(c)}))
        if (dest in sees) != (src in sees):
            changed.append({"id": c.get("id"), "name": c.get("name"), "students": len(c.get("emails") or []),
                            "gains" if dest in sees else "loses": [video["title"]]})
    placed = sum(1 for v in videos if tree.section_of(v) == dest)
    plan = {"operation": "move_library_video", "args": {"video_id": video_id, "section": dest}, "video_id": video_id,
            "title": video["title"], "url": video["url"], "section": dest, "from": src, "before": tree.label(src),
            "after": tree.label(dest), "videos_affected": 1, "sub_sections_affected": 0,
            "position_in_destination": placed + 1, "keeps_title_and_url": True,
            "access": {"classes": changed, "tiers_that_see_every_section": _tiers(auth),
                       "rule": "כיתה רואה את הסרטון לפי המדור שבו הוא עומד"}}
    plan["state"] = {**_fingerprint(tree, videos, [src, dest], [src, dest]), "video": (video["title"], video["url"], src),
                     "classes": changed}
    return plan


PLANNERS = {"rename_library_section": lambda auth, a: plan_rename(auth, a["section"], a["name"]),
            "move_library_section": lambda auth, a: plan_move_section(auth, a["section"], a["new_parent"]),
            "delete_library_section": lambda auth, a: plan_delete(auth, a["section"]),
            "move_library_video": lambda auth, a: plan_move_video(auth, a["video_id"], a["section"])}


def apply(auth, plan: dict, by: str) -> dict[str, Any]:
    """Writes what a plan describes, if everything it looked at is as it saw it."""
    if plan.get("operation") not in PLANNERS:
        raise LibraryError("הפעולה אינה מוכרת")
    changed = LibraryError("המדורים, הסרטונים או הגישה של הכיתות השתנו מאז ההכנה. הכינו את הפעולה מחדש.")
    try:
        fresh = PLANNERS[plan["operation"]](auth, plan["args"])
    except LibraryUnavailable:
        raise
    except LibraryError as exc:   # what was fine at the preview no longer is: a name taken, a section no longer empty
        raise LibraryError(f"{exc} (המצב השתנה מאז ההכנה)") from exc
    if fresh["state"] != plan["state"]:
        raise changed
    op, sid = plan["operation"], plan.get("section")
    try:
        if op == "move_library_video":
            library.move(auth, plan["video_id"], plan["section"], last=True)
            return {"video_id": plan["video_id"], "title": plan["title"], "section": plan["section"],
                    "section_name": library.label(plan["section"]), "position": plan["position_in_destination"]}
        if not auth.library_sections(True):
            auth.seed_library_sections(library.SEED_ROWS)   # the built-in sections become rows before one is changed
        if op == "rename_library_section":
            auth.update_library_section(sid, {"name": plan["new_name"]})
        elif op == "move_library_section":
            if plan.get("new_parent") and plan.get("keep_parent_flag"):
                auth.set_library_section_holds(plan["new_parent"], True)   # its videos stay in it, and it stays a target
            auth.update_library_section(sid, {"parent_id": plan["new_parent"],
                                              "ord": len(_siblings(library.tree(), plan["new_parent"] or ""))})
        else:
            _need_deleted_column(auth)
            for c in plan["removed_from_class_assignments"]:   # first the classes, so none keeps pointing at it
                row = next((x for x in auth.classes(True) if x.get("id") == c["id"]), None)
                if row is None or sid not in (row.get("library_sections") or []):
                    continue
                after = teacher_operations._normalized_row(
                    {**row, "library_sections": [s for s in row["library_sections"] if s != sid]})
                auth.save_class(row["id"], after["name"], after["emails"], after["nicknames"], after["lessons"],
                                after["homeworks"], after["dailies"], after["subject"], teachers=after["teachers"],
                                library_sections=after["library_sections"],
                                **({"meetings": after["meetings"]} if after.get("meetings") or row.get("meetings") else {}))
            auth.update_library_section(sid, {"deleted": True})
    except ClassConflict as exc:
        raise LibraryError("כבר יש מדור באותו שם באותו מקום") from exc
    except (AuthUnavailable, KeyError) as exc:
        raise LibraryUnavailable(f"לא ניתן לשמור את השינוי: {exc}") from exc
    library.refresh(auth, fresh=True, strict=True)
    return {"section": sid, "section_name": library.label(sid) if library.has_node(sid) else None,
            "before": plan["before"], "after": plan["after"], "videos_affected": plan["videos_affected"],
            "sub_sections_affected": plan["sub_sections_affected"]}
