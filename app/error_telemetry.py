"""Privacy-safe classification at explicit operational failure boundaries."""
from __future__ import annotations

from typing import Any


BOUNDARIES = {
    "live_session": "live_connection_failure",
    "authentication": "authentication",
    "database": "database",
    "model": "upstream_service",
    "text_to_speech": "upstream_service",
    "mcp": "internal",
    "application": "internal",
}


def record_failure(auth: Any, component: str, *, timed_out: bool = False, rejected: bool = False) -> bool:
    """Record one enum-only failure without accepting an exception or request value.
    `rejected`: the service refused the call (HTTP 429, rate limited) rather than failing."""
    if component not in BOUNDARIES:
        raise ValueError("unsupported operational failure boundary")
    category = "timeout" if timed_out else BOUNDARIES[component]
    outcome = ("timed_out" if timed_out else "rejected" if rejected
               else "unavailable" if component != "live_session" else "exception")
    writer = getattr(auth, "record_operational_error", None)
    return bool(writer and writer(category, component, outcome))
