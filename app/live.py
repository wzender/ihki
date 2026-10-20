"""Temporary live-class state.

Live sessions deliberately live in process memory: class/teacher assignments are durable in
Supabase, while attendance, questions and answers disappear when a session ends or the service
restarts.  The browser polls this store; no recording bytes are retained here.
"""
from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime, timezone


SESSION_TTL = 2 * 60 * 60
PRESENCE_TTL = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LiveConflict(ValueError):
    pass


class LiveStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = {}

    def _prune(self, now: float) -> None:
        for code, session in list(self._sessions.items()):
            if session["ended"] or now - session["teacher_seen"] > SESSION_TTL:
                self._sessions.pop(code, None)

    def _new_code(self) -> str:
        for _ in range(100):
            code = f"{secrets.randbelow(1_000_000):06d}"
            if code not in self._sessions:
                return code
        raise RuntimeError("could not allocate a live-session code")

    def start(self, course: dict, teacher: dict) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            existing = next((s for s in self._sessions.values() if s["class_id"] == course["id"]), None)
            if existing:
                existing["teacher_seen"] = now
                return self._public(existing)
            code = self._new_code()
            roster = {email: (course.get("nicknames") or {}).get(email) or email.split("@", 1)[0]
                      for email in course.get("emails", [])}
            session = {"code": code, "class_id": course["id"], "class_name": course["name"],
                       "teacher_id": teacher["id"], "teacher_email": teacher["email"].lower(),
                       "started_at": _now_iso(), "teacher_seen": now, "ended": False,
                       "question": None, "participants": {}, "roster": roster, "answers": {}, "question_no": 0}
            self._sessions[code] = session
            return self._public(session)

    def list_for(self, class_ids: set[int], user_id: str) -> list[dict]:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return [self._public(s) for s in self._sessions.values()
                    if s["class_id"] in class_ids or user_id in s["participants"]]

    def get(self, code: str) -> dict | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            return self._sessions.get(code)

    def join(self, code: str, user: dict, name: str = "") -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(code)
            if not session:
                raise KeyError(code)
            session["participants"][user["id"]] = {"email": user["email"].lower(),
                                                      "name": name or user["email"].split("@", 1)[0],
                                                      "last_seen": now}
            return self._public(session)

    def heartbeat(self, code: str, user: dict, teacher: bool = False) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(code)
            if not session:
                raise KeyError(code)
            if teacher:
                session["teacher_seen"] = now
            elif user["id"] in session["participants"]:
                session["participants"][user["id"]]["last_seen"] = now
            else:
                raise PermissionError(code)
            return self._detail(session, user["id"], teacher, now)

    def ask(self, code: str, phrase: dict) -> dict:
        now = time.monotonic()
        with self._lock:
            session = self._sessions.get(code)
            if not session:
                raise KeyError(code)
            variants = phrase.get("variants") or []
            arabic = phrase.get("ar") or (variants[0][0] if variants else "")
            session["question_no"] += 1
            session["question"] = {"no": session["question_no"], "phrase_id": phrase["id"],
                                   "he": phrase["he"], "ar": arabic, "tr": phrase["tr"],
                                   "started_at": _now_iso()}
            session["answers"] = {}
            session["teacher_seen"] = now
            return self._public(session)

    def begin_answer(self, code: str, user_id: str) -> dict:
        with self._lock:
            session = self._sessions.get(code)
            if not session or not session["question"]:
                raise KeyError(code)
            if user_id not in session["participants"]:
                raise PermissionError(code)
            if user_id in session["answers"]:
                raise LiveConflict("one attempt only")
            session["answers"][user_id] = {"pending": True}
            return dict(session["question"])

    def cancel_answer(self, code: str, user_id: str) -> None:
        with self._lock:
            session = self._sessions.get(code)
            if session and session["answers"].get(user_id, {}).get("pending"):
                session["answers"].pop(user_id, None)

    def finish_answer(self, code: str, user_id: str, answer: dict) -> None:
        with self._lock:
            session = self._sessions.get(code)
            if not session or user_id not in session["answers"]:
                raise KeyError(code)
            session["answers"][user_id] = answer

    def end(self, code: str) -> None:
        with self._lock:
            if code not in self._sessions:
                raise KeyError(code)
            self._sessions.pop(code, None)

    @staticmethod
    def _public(session: dict) -> dict:
        return {k: session[k] for k in ("code", "class_id", "class_name", "started_at", "question")}

    def _detail(self, session: dict, user_id: str, teacher: bool, now: float) -> dict:
        base = self._public(session)
        if teacher:
            participants = []
            joined_emails = set()
            for uid, person in session["participants"].items():
                joined_emails.add(person["email"])
                answer = session["answers"].get(uid)
                participants.append({"id": uid, "name": person["name"], "email": person["email"],
                                     "online": now - person["last_seen"] <= PRESENCE_TTL,
                                     "answer": None if not answer or answer.get("pending") else answer})
            participants += [{"id": "", "name": name, "email": email, "online": False, "answer": None}
                             for email, name in session["roster"].items() if email not in joined_emails]
            return {**base, "participants": participants}
        answer = session["answers"].get(user_id)
        return {**base, "answered": user_id in session["answers"],
                "answer": None if not answer or answer.get("pending") else answer}
