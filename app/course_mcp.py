"""The MCP's tools for the course's workbook (add-teacher-course-authoring, group 4).

Read: get_course_outline, get_course_topic. Change the draft: prepare_course_change → publish_course_change (one
draft operation, or add_word to the course's dictionary). Publish a version to learners: prepare_course_publish →
publish_course_version. Every change is prepared first and shows exactly what it does; the approval is single-use,
bound to the manager who prepared it and good for ten minutes, and the change is refused when the draft moved since.
Only holders of course.edit (course.publish to publish a version): the Arabic course's managers and developers. None
of these tools touch accounts, roles or classes.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Literal

from . import course, course_draft, course_store
from .access import has_permission
from .auth import AuthUnavailable, ClassConflict

log = logging.getLogger("ihki.course_mcp")

DRAFT_OPERATIONS = ("add_items", "update_item", "delete_items", "move_item", "replace_topic_items", "set_generator",
                    "add_topic", "update_topic", "delete_topic", "move_topic", "add_lesson")
OPERATIONS = DRAFT_OPERATIONS + ("add_word",)
STALE = "הטיוטה השתנתה מאז שהשינוי הוכן. הכינו אותו מחדש"
INVALID = "האישור אינו תקף, פג תוקף, שייך למשתמש אחר או כבר נוצל."
BUSY = "בוצעו יותר מדי פעולות. נסו שוב בעוד דקה."

CHANGE_HELP = (
    "`change` holds the operation's fields (a topic is named by its id, `cat`, from get_course_outline, e.g. "
    "\"l1-say\"; an item by its `id` from get_course_topic; places count from 0): "
    "add_items {cat, items: [{he, ar | tr, he_say?}], at?}; update_item {cat, id, he?, ar?, tr?, he_say?}; "
    "delete_items {cat, ids}; move_item {cat, id, to}; "
    "replace_topic_items {cat, items: [{he, ar | tr, he_say?}]} (a whole topic from a revised workbook page: each "
    "incoming item is matched to an existing one by its Hebrew, and its place, and keeps its id and key, so its "
    "learners' progress; unmatched existing items are deleted); set_generator {cat, args} (a conjugation table's word "
    "list, as get_course_topic shows it); add_topic {level, n, tid, name, kind: learn|match|say|read|write, ordered?, "
    "at?}; update_topic {cat, name?, ordered?}; delete_topic {cat}; move_topic {cat, to}; add_lesson {level, n}; "
    "add_word {word (Arabic letters), tr (Hebrew letters, pointed), meaning}. An item's Arabic is written in Arabic "
    "letters (ar) or in Hebrew letters (tr), one of them; a word in Hebrew letters the course's dictionary does not "
    "have is read by rule and listed in `guessed`.")


def _item(p: dict) -> dict:
    """A compiled sentence as a chat shows it: no pronunciations."""
    return {"id": p["id"], "key": p["key"], "he": p["he"], "tr": p["tr"], "ar": p["variants"][0][0] if p.get("variants") else "",
            **({"he_say": p["he_say"]} if p.get("he_say") else {}), **({"speaker": p["speaker"]} if p.get("speaker") else {})}


def _summary(d: dict) -> dict:
    """A diff's counts, for a preview's headline."""
    return {k: len(v) for k, v in d.items() if k != "guessed" and isinstance(v, list)}


def register(tool: Callable, *, app, actor: Callable[[], dict | None], limiter, approvals, read, ttl: int) -> None:
    """Adds the course tools to the MCP server (`tool`: grok_mcp's decorator; `read`: its bounded read)."""

    def editor(permission: str = "course.edit"):
        current = actor()
        store = getattr(app.state, "course", None)
        if current is None:
            return None, None, "אין הרשאת ניהול"
        if not has_permission(current, permission):
            return None, None, ("רק מנהלי קורס הערבית ומפתחים משנים את חוברת הקורס" if permission == "course.edit"
                                else "רק מנהלי קורס הערבית ומפתחים מפרסמים גרסה של החוברת")
        if store is None:
            return None, None, "גרסאות החוברת זמינות רק עם Supabase"
        if store.setup:
            return None, None, "טבלאות החוברת עוד לא נוצרו ב-Supabase. הריצו את ה-SQL שבכרטיס 'חוברת הקורס' בניטור"
        return current, store, ""

    def audit(action: str, current: dict, outcome: str, reason: str = "", label: str = "") -> None:
        log.info("MCP course action=%s outcome=%s reason=%s", action, outcome, reason)
        store = getattr(app.state, "auth", None)
        if outcome == "success" and store is not None and hasattr(store, "log_event"):
            store.log_event("content", current, label or action, {"action": action, "via": "mcp"})

    async def guarded(fn):
        try:
            return await read(fn)
        except course.CourseError as e:
            return {"ok": False, "error": str(e)}
        except ClassConflict:
            return {"ok": False, "error": STALE}
        except AuthUnavailable as e:
            return {"ok": False, "error": f"Supabase לא זמין: {e}"}

    # ---- reading ----

    @tool(description=("The course's workbook as a tree, without its sentences: levels, their written lessons, and "
                       "each lesson's topics (id such as \"l1-say\", name, kind, how many items, and whether it is a "
                       "conjugation table). of=\"draft\" (the default) reads the draft being edited, or the "
                       "published course while there is no draft; of=\"published\" reads what learners have now. "
                       "Read-only; Arabic-course managers and developers."), structured_output=True)
    async def get_course_outline(of: Literal["draft", "published"] = "draft", level: int | None = None) -> dict[str, Any]:
        current, store, error = editor()
        if current is None:
            return {"ok": False, "error": error}

        def run():
            row = store.draft() if of == "draft" else None
            doc = row["document"] if row else store._published_doc()
            return {"ok": True, "of": "draft" if row else "published",
                    "draft": {"number": row["number"], "updated_at": row["updated_at"]} if row else None,
                    "published_number": store.number, "levels": course_store.outline(doc, level)}
        return await guarded(run)

    @tool(description=("One topic of the course's workbook with its items: each item's id (refer to items by it), "
                       "Hebrew, Arabic (in Arabic letters, or in Hebrew letters as tr) and he_say; for a conjugation "
                       "table, its word list (generator.args) and the forms it makes; and `guessed`, the words read by "
                       "rule because the course's dictionary does not have them. of=\"draft\" (default) or "
                       "\"published\". Read-only."), structured_output=True)
    async def get_course_topic(cat: str, of: Literal["draft", "published"] = "draft") -> dict[str, Any]:
        current, store, error = editor()
        if current is None:
            return {"ok": False, "error": error}

        def run():
            row = store.draft() if of == "draft" else None
            doc = row["document"] if row else store._published_doc()
            lesson, topic = course_draft._find_topic(doc, cat)
            compiled = course.compile_course(doc)
            stored = {k: v for k, v in topic.items() if k not in ("items", "ids")}
            return {"ok": True, "of": "draft" if row else "published",
                    "updated_at": row["updated_at"] if row else None, "topic": {"cat": cat, **stored},
                    "items": [_item(p) for p in compiled.phrases if p["cat"] == cat],
                    "guessed": compiled.guessed.get(cat, [])}
        return await guarded(run)

    # ---- changing the draft ----

    @tool(description=("Prepare one change to the course's draft (made from the published course when there is no "
                       "draft yet). Learners see nothing until a version is published (prepare_course_publish). "
                       "Returns a ten-minute approval and the exact effect: the topic's items after the change, what "
                       "is added, changed, moved and deleted (with how many learners practised each changed or "
                       "deleted item: their progress on a deleted item stops counting), and the words read by rule. "
                       "Changes nothing. Publish with publish_course_change only after the manager confirms. "
                       + CHANGE_HELP), structured_output=True)
    async def prepare_course_change(operation: Literal[OPERATIONS], change: dict[str, Any]) -> dict[str, Any]:
        current, store, error = editor()
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            audit("prepare_course_change", current, "refused", "rate_limit")
            return {"ok": False, "error": BUSY}
        change = dict(change or {})
        if operation == "add_word":
            word, tr = str(change.get("word") or "").strip(), str(change.get("tr") or "").strip()
            if not word or not tr:
                return {"ok": False, "error": "כתבו את המילה באותיות ערביות (word) ואת התעתיק שלה באותיות עבריות (tr)"}
            if course.in_base_lexicon(word):
                return {"ok": False, "error": f"המילה {word} כבר במילון הקורס"}
            plan = {"operation": "course_word", "word": word, "tr": tr, "meaning": str(change.get("meaning") or "").strip()}
            approval_id, _ = approvals.prepare(str(current["id"]), plan)
            audit("prepare_course_change", current, "prepared")
            return {"ok": True, "approval_id": approval_id, "operation": operation,
                    "preview": {"word": word, "tr": tr, "meaning": plan["meaning"],
                                "pronunciation": "נקראת לפי כללי התעתיק עד שמפתח יאשר את ההגייה",
                                "note": "המילה נוספת למילון הקורס לכל הטיוטות מיד, ולא דרך פרסום גרסה"},
                    "expires_in_seconds": ttl, "published": False}
        op = {**{k: v for k, v in change.items() if k != "op"},
              "op": "replace_items" if operation == "replace_topic_items" else operation}

        def run():
            doc, row = store.working()
            new, report = course_draft.apply(doc, op)
            effect = store.with_learners(course_draft.diff(doc, new))
            if course_draft.empty(effect):
                raise course.CourseError("השינוי הזה לא משנה דבר בחוברת")
            plan = {"operation": "course_change", "op": op,
                    "draft": row["number"] if row else None, "expected": row["updated_at"] if row else None}
            approval_id, _ = approvals.prepare(str(current["id"]), plan)
            effect.pop("guessed", None)
            return {"ok": True, "approval_id": approval_id, "operation": operation,
                    "draft": {"number": row["number"], "updated_at": row["updated_at"]} if row else
                             {"number": None, "note": "אין עדיין טיוטה: השינוי ייצור אותה מהגרסה המפורסמת"},
                    "topic": report["cat"], "summary": _summary(effect), "effect": effect,
                    "items_after": [_item(p) for p in report["items"]], "guessed": report["guessed"],
                    "expires_in_seconds": ttl, "published": False}
        answer = await guarded(run)
        audit("prepare_course_change", current, "prepared" if answer.get("ok") else "refused")
        return answer

    @tool(description=("Make a prepared change to the course's draft (or add the prepared word to the course's "
                       "dictionary) after the manager explicitly confirms the shown effect. Single-use, bound to the "
                       "manager who prepared it, ten minutes, and refused when the draft changed meanwhile (prepare "
                       "again). Learners still see the published course; preview the draft in the app (תצוגה כתלמיד) "
                       "and publish it with prepare_course_publish."), structured_output=True)
    async def publish_course_change(approval_id: str) -> dict[str, Any]:
        current, store, error = editor()
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            audit("publish_course_change", current, "refused", "rate_limit")
            return {"ok": False, "error": BUSY}
        item = approvals.consume(approval_id, str(current["id"]))
        if item is None or item.plan.get("operation") not in ("course_change", "course_word"):
            audit("publish_course_change", current, "refused", "invalid_approval")
            return {"ok": False, "error": INVALID}
        plan, by = item.plan, str(current.get("email") or "")
        if plan["operation"] == "course_word":
            def add():
                row = store.add_word(plan["word"], plan["tr"], plan["meaning"], by)
                return {"ok": True, "word": row["word"], "tr": row["tr"], "published": True}
            answer = await guarded(add)
            audit("publish_course_change", current, "success" if answer.get("ok") else "refused",
                  label=f"מילה למילון הקורס: {plan['word']}")
            return answer

        def run():
            row = store.draft()
            if (row["updated_at"] if row else None) != plan["expected"]:
                raise course.CourseError(STALE)
            out = store.change(plan["op"], by, plan["expected"])
            return {"ok": True, "draft": {"number": out["number"], "updated_at": out["updated_at"]},
                    "topic": out["cat"], "items_after": [_item(p) for p in out["items"]], "guessed": out["guessed"],
                    "published": True, "learners_see_it": False}
        answer = await guarded(run)
        audit("publish_course_change", current, "success" if answer.get("ok") else "refused",
              label=f"שינוי בטיוטת החוברת: {plan['op']['op']} {plan['op'].get('cat') or ''}".strip())
        return answer

    # ---- publishing a version ----

    @tool(description=("Prepare publishing a version of the course to every learner: the draft (default), or an "
                       "earlier version by its number to roll back. Shows what it changes against the course "
                       "learners have now (lessons, topics and items added, changed, moved and deleted, with learner "
                       "counts) and the classes it changes for (a lesson they study that it does not write, a homework "
                       "topic it drops). Changes nothing; returns a ten-minute approval for publish_course_version, "
                       "to use only after the manager explicitly confirms."), structured_output=True)
    async def prepare_course_publish(number: int | None = None) -> dict[str, Any]:
        current, store, error = editor("course.publish")
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            audit("prepare_course_publish", current, "refused", "rate_limit")
            return {"ok": False, "error": BUSY}

        def run():
            if number is None:
                row = store.draft()
                if row is None:
                    raise course.CourseError("אין טיוטה לפרסם. כדי לחזור לגרסה קודמת, ציינו את מספרה")
            else:
                row = store.auth.course_version(int(number))
                if row is None:
                    raise course.CourseError(f"אין גרסה {number}")
            published = store.auth.course_published()
            if published and published["number"] == row["number"]:
                raise course.CourseError(f"גרסה {row['number']} היא כבר הגרסה המפורסמת")
            effect = store.with_learners(course_draft.diff(store._published_doc(), row["document"]))
            guessed = effect.pop("guessed", {})
            affected = course_store.affected_classes(row["document"], store.auth.classes(fresh=True))
            plan = {"operation": "course_publish", "number": row["number"], "status": row["status"],
                    "updated_at": row.get("updated_at"), "published": published["number"] if published else None}
            approval_id, _ = approvals.prepare(str(current["id"]), plan)
            return {"ok": True, "approval_id": approval_id, "number": row["number"], "status": row["status"],
                    "replaces": plan["published"], "summary": _summary(effect), "effect": effect,
                    "words_read_by_rule": sum(len(v) for v in guessed.values()),
                    "affected_classes": affected, "expires_in_seconds": ttl, "published": False}
        answer = await guarded(run)
        audit("prepare_course_publish", current, "prepared" if answer.get("ok") else "refused")
        return answer

    @tool(description=("Publish the prepared version to every learner (within half a minute) after the manager "
                       "explicitly confirms the shown effect and affected classes. Single-use, bound to the manager "
                       "who prepared it, ten minutes, and refused when the draft or the published version changed "
                       "meanwhile. The version it replaces is kept, and can be published again to roll back."),
          structured_output=True)
    async def publish_course_version(approval_id: str) -> dict[str, Any]:
        current, store, error = editor("course.publish")
        if current is None:
            return {"ok": False, "error": error}
        if not limiter.allow():
            audit("publish_course_version", current, "refused", "rate_limit")
            return {"ok": False, "error": BUSY}
        item = approvals.consume(approval_id, str(current["id"]))
        if item is None or item.plan.get("operation") != "course_publish":
            audit("publish_course_version", current, "refused", "invalid_approval")
            return {"ok": False, "error": INVALID}
        plan = item.plan

        def run():
            row = store.auth.course_version(plan["number"])
            published = store.auth.course_published()
            if row is None or row.get("updated_at") != plan["updated_at"] or \
                    (published["number"] if published else None) != plan["published"]:
                raise course.CourseError("הגרסה או הגרסה המפורסמת השתנו מאז ההכנה. הכינו את הפרסום מחדש")
            store.publish(plan["number"], str(current.get("email") or ""))
            return {"ok": True, "number": plan["number"], "published": store.number == plan["number"],
                    "replaced": plan["published"], "learners_get_it_within_seconds": course_store.POLL_SECONDS}
        answer = await guarded(run)
        audit("publish_course_version", current, "success" if answer.get("ok") else "refused",
              label=f"פרסום גרסה {plan['number']} של החוברת")
        return answer
