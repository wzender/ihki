#!/usr/bin/env python3
"""Read-only check: may these accounts use the MCP connector, and what will they see?

Usage:  python scripts/check_mcp_access.py a@example.com b@example.com ...

Reads SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, SUPABASE_SECRET_KEY, TEACHER_EMAILS and DEVELOPER_EMAILS
from the environment or .env, exactly as the server does. It runs the same decision the MCP server makes
(app.grok_mcp.mcp_access_decision) for each account. It sends only GET requests, never changes data,
and prints no keys or tokens.

TEACHER_EMAILS and DEVELOPER_EMAILS are read from *this* machine. If the Hugging Face Space sets them
differently (Space → Settings → Variables and secrets), pass the Space's values with --teachers / --developers.

For each email it prints:
  mcp      ALLOWED or DENIED, and why (manager | class_teacher | not_in_any_class |
           no_management_or_teaching | never_signed_in)
  source   where manager rights come from: TEACHER_EMAILS, DEVELOPER_EMAILS, user_access roles
  subject  the manager's subject; a non-developer manager with no subject sees no classes over MCP
  classes  how many classes the MCP tools will show this account
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
except ImportError:  # optional, as in the server
    load_dotenv = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("emails", nargs="+")
    parser.add_argument("--teachers", help="override TEACHER_EMAILS (comma or space separated)")
    parser.add_argument("--developers", help="override DEVELOPER_EMAILS (comma or space separated)")
    args = parser.parse_args()
    if load_dotenv:
        load_dotenv()
    if args.teachers is not None:
        os.environ["TEACHER_EMAILS"] = args.teachers
    if args.developers is not None:
        os.environ["DEVELOPER_EMAILS"] = args.developers

    from app.access import resolve_user_access
    from app.auth import AuthUnavailable, load_auth
    from app.class_management import visible_classes
    from app.grok_mcp import mcp_access_decision

    auth = load_auth()
    if auth is None:
        print("Supabase is not configured here (SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY missing).")
        return 2
    if not auth.secret:
        print("SUPABASE_SECRET_KEY is not set here: roles and classes cannot be read.")
        return 2
    try:
        accounts = {str(u["email"]).strip().lower(): u for u in auth.users() if u.get("email")}
    except AuthUnavailable as exc:
        print(f"Could not list accounts: {type(exc).__name__}")
        return 2

    failures = 0
    for email in (e.strip().lower() for e in args.emails):
        account = accounts.get(email)
        if account is None:
            print(f"{email}\n  mcp: DENIED (never_signed_in: no Supabase account yet; they must sign in once)\n")
            failures += 1
            continue
        user = {"id": account["id"], "email": email}
        try:
            identity = resolve_user_access(auth, user, fresh_permissions=True)
            row = auth.access_for(account["id"], fresh=True)
        except AuthUnavailable as exc:
            print(f"{email}\n  mcp: UNKNOWN (Supabase unavailable: {type(exc).__name__})\n")
            failures += 1
            continue
        allowed, reason = mcp_access_decision(identity)
        sources = [name for name, members in (("TEACHER_EMAILS", auth.teachers),
                                              ("DEVELOPER_EMAILS", auth.developers)) if email in members]
        row_roles = sorted((row or {}).get("roles") or [])
        if {"admin", "developer"} & set(row_roles):
            sources.append(f"user_access roles {row_roles}")
        try:
            classes = len(visible_classes(auth, identity, fresh=True)) if allowed else 0
        except Exception as exc:  # report, keep going
            classes = f"unavailable ({type(exc).__name__})"
        subject = identity.get("subject") or ""
        is_dev = "developer" in identity.get("roles", ())
        print(email)
        print(f"  mcp: {'ALLOWED' if allowed else 'DENIED'} ({reason})")
        print(f"  roles: {', '.join(identity.get('roles', [])) or '-'}")
        print(f"  source: {', '.join(sources) or 'none (not a manager)'}")
        print(f"  subject: {subject or ('all (developer)' if is_dev else 'NOT SET')}")
        print(f"  classes visible over MCP: {classes}")
        if allowed and not is_dev and not subject and reason == "manager":
            print("  note: manager with no subject; set one in the panel (ניטור) or the MCP tools show no classes")
        print()
        failures += 0 if allowed else 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
