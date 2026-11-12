"""Every kind the app logs must be allowed by system_events' check constraint, or Supabase silently refuses the row."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = re.compile(r"check \(kind in \(([^)]*)\)\)")


def allowed(text: str) -> set[str]:
    lists = [set(re.findall(r"'(\w+)'", m)) for m in ALLOWED.findall(text) if "'exercise'" in m]
    assert lists, "no system_events kind constraint found"
    return set.intersection(*lists)


def test_every_logged_kind_is_allowed_by_the_table():
    logged = {k for f in (ROOT / "app").glob("*.py") for k in re.findall(r"log_event\(\s*[\"'](\w+)[\"']", f.read_text())}
    assert logged
    for source in ("docs/mathly-supabase.sql", "docs/system-events-kinds-migration.sql", "app/main.py"):
        missing = logged - allowed((ROOT / source).read_text())
        assert not missing, f"{source} does not allow {sorted(missing)}"
