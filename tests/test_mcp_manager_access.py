"""Managers can always use the MCP connector, however they were made managers."""
import asyncio
from types import SimpleNamespace

import pytest

from app.grok_mcp import SupabaseTokenVerifier, mcp_access_decision
from app.access import resolve_user_access

MANAGERS = ["wzwzchatgpt@gmail.com", "a0524674224@gmail.com", "meravcohenbr@gmail.com",
            "office@ahalawasahala.com"]


class Store:
    """A Supabase stand-in: one token per email, classes that do not include the managers."""

    def __init__(self, *, teachers=(), developers=(), access=None, email_case=str.lower):
        self.teachers = frozenset(teachers)
        self.developers = frozenset(developers)
        self.tutors = frozenset()
        self.access = access or {}
        self.email_case = email_case

    def user(self, token):
        email = token.removeprefix("token-")
        return {"id": f"id-{email}", "email": self.email_case(email)} if email in MANAGERS + ["s@x.com"] else None

    def classes(self, fresh=False):
        # Non-empty, so an account outside every class would be denied unless privileged.
        return [{"id": 1, "name": "א", "subject": "ערבית", "emails": ["s@x.com"], "teachers": []}]

    def access_for(self, user_id, fresh=False):
        return self.access.get(user_id)

    def ranked(self, fresh=False):
        return frozenset()


def verify(store, email):
    verifier = SupabaseTokenVerifier(SimpleNamespace(state=SimpleNamespace(auth=store)))
    return asyncio.run(verifier.verify_token(f"token-{email}"))


@pytest.mark.parametrize("email", MANAGERS)
def test_manager_from_teacher_emails_variable(email):
    accepted = verify(Store(teachers=MANAGERS), email)
    assert accepted and "management.access" in accepted.claims["ihki_actor"]["permissions"]


@pytest.mark.parametrize("email", MANAGERS)
def test_manager_from_panel_admin_role(email):
    store = Store(access={f"id-{e}": {"roles": ["admin"], "entitlements": [], "subject": "ערבית"} for e in MANAGERS})
    accepted = verify(store, email)
    assert accepted and accepted.claims["ihki_actor"]["subject"] == "ערבית"


@pytest.mark.parametrize("email", MANAGERS)
def test_manager_without_a_subject_still_connects(email):
    store = Store(access={f"id-{e}": {"roles": ["admin"], "entitlements": [], "subject": ""} for e in MANAGERS})
    assert verify(store, email)


@pytest.mark.parametrize("email", MANAGERS)
def test_manager_email_case_from_supabase_does_not_matter(email):
    assert verify(Store(teachers=MANAGERS, email_case=str.upper), email)


@pytest.mark.parametrize("email", MANAGERS)
def test_teacher_emails_override_a_student_only_access_row(email):
    store = Store(teachers=MANAGERS,
                  access={f"id-{e}": {"roles": ["student"], "entitlements": [], "subject": ""} for e in MANAGERS})
    assert verify(store, email)


def test_a_plain_student_is_still_refused():
    assert verify(Store(teachers=MANAGERS), "s@x.com") is None


def test_decision_reasons():
    store = Store(teachers=MANAGERS)
    assert mcp_access_decision(resolve_user_access(store, store.user(f"token-{MANAGERS[1]}"))) == (True, "manager")
    assert mcp_access_decision(resolve_user_access(store, store.user("token-s@x.com"))) == (
        False, "no_management_or_teaching")
    assert mcp_access_decision(None) == (False, "unknown_user")
