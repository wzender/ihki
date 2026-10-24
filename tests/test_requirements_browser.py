from __future__ import annotations

import json
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scripts.requirements_browser import (BrowserError, OpenSpecClient, build_snapshot, create_server,
                                          parse_markdown, safe_read)


def completed(value, returncode=0, stderr=""):
    stdout = value if isinstance(value, str) else json.dumps(value)
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def fixture_root(tmp_path: Path) -> Path:
    (tmp_path / "openspec" / "changes" / "demo").mkdir(parents=True)
    (tmp_path / "openspec" / "config.yaml").write_text("schema: spec-driven\n", encoding="utf-8")
    (tmp_path / "openspec" / "changes" / "demo" / "proposal.md").write_text(
        "# Proposal\n\n## Why\n\nInspect <script>alert(1)</script>.\n\n- [ ] pending\n", encoding="utf-8")
    return tmp_path


def fake_executor(root: Path, broken_spec: bool = False):
    def execute(argv: list[str], cwd: Path):
        args = tuple(argv[1:])
        values = {
            ("context", "--json"): {"root": {"path": str(root)}},
            ("list", "--specs", "--json"): {"specs": [{"id": "alpha", "requirementCount": 1}]},
            ("show", "alpha", "--type", "spec", "--json"): {
                "title": "Alpha", "overview": "First capability", "requirements": [{
                    "name": "Visible", "text": "The tool SHALL show it.",
                    "scenarios": [{"name": "Open", "rawText": "- **WHEN** opened\n- **THEN** shown"}],
                }],
            },
            ("list", "--json"): {"changes": [{"name": "demo", "completedTasks": 0, "totalTasks": 1}]},
            ("status", "--change", "demo", "--json"): {
                "schemaName": "spec-driven", "artifactPaths": {"proposal": {
                    "existingOutputPaths": [str(root / "openspec" / "changes" / "demo" / "proposal.md")]
                }}
            },
        }
        if broken_spec and args[:2] == ("show", "alpha"):
            return completed("not json")
        return completed(values[args])
    return execute


def test_client_resolves_root_and_reports_missing_cli(tmp_path):
    root = fixture_root(tmp_path)
    assert OpenSpecClient(root, fake_executor(root)).root() == root

    def missing(argv, cwd):
        raise FileNotFoundError

    with pytest.raises(BrowserError, match="not installed or is not on PATH"):
        OpenSpecClient(root, missing).root()


def test_client_reports_missing_root(tmp_path):
    def no_root(argv, cwd):
        return completed({"root": None})

    with pytest.raises(BrowserError, match="No OpenSpec root"):
        OpenSpecClient(tmp_path, no_root).root()


def test_snapshot_contains_specs_changes_tasks_and_inert_text(tmp_path):
    root = fixture_root(tmp_path)
    snapshot = build_snapshot(OpenSpecClient(root, fake_executor(root)))
    assert snapshot["summary"] == {"specifications": 1, "requirements": 1, "changes": 1}
    assert snapshot["specs"][0]["requirements"][0]["scenarios"][0]["name"] == "Open"
    blocks = snapshot["changes"][0]["artifacts"][0]["blocks"]
    assert any(block["type"] == "task" and not block["done"] for block in blocks)
    assert any("<script>alert(1)</script>" in block.get("text", "") for block in blocks)


def test_snapshot_keeps_other_content_when_one_spec_is_malformed(tmp_path):
    root = fixture_root(tmp_path)
    snapshot = build_snapshot(OpenSpecClient(root, fake_executor(root, broken_spec=True)))
    assert snapshot["changes"][0]["id"] == "demo"
    assert snapshot["specs"] == []
    assert "invalid JSON" in snapshot["warnings"][0]["message"]


def test_snapshot_keeps_specs_when_one_artifact_is_unreadable(tmp_path):
    root = fixture_root(tmp_path)
    (root / "openspec" / "changes" / "demo" / "proposal.md").unlink()
    snapshot = build_snapshot(OpenSpecClient(root, fake_executor(root)))
    assert snapshot["specs"][0]["id"] == "alpha"
    assert snapshot["changes"][0]["artifacts"] == []
    assert "Could not read" in snapshot["warnings"][0]["message"]


def test_safe_read_refuses_paths_and_symlinks_outside_root(tmp_path):
    root = fixture_root(tmp_path / "project")
    outside = tmp_path / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    with pytest.raises(BrowserError, match="outside"):
        safe_read(root, str(outside))
    link = root / "openspec" / "linked.md"
    link.symlink_to(outside)
    with pytest.raises(BrowserError, match="outside"):
        safe_read(root, str(link))


def test_parser_extracts_headings_lists_and_tasks():
    assert parse_markdown("# A\n\nText\n\n- item\n- [x] done") == [
        {"type": "heading", "level": 1, "text": "A"},
        {"type": "paragraph", "text": "Text"},
        {"type": "list", "text": "item"},
        {"type": "task", "done": True, "text": "done"},
    ]


def test_browser_client_uses_safe_dom_rendering():
    script = Path("scripts/requirements_browser.js").read_text(encoding="utf-8")
    assert "textContent" in script
    assert "innerHTML" not in script
    html = Path("scripts/requirements_browser.html").read_text(encoding="utf-8")
    assert 'id="specNav"' in html
    assert 'id="changeNav"' in html
    assert 'id="search"' in html


def running_server(tmp_path: Path):
    page = tmp_path / "page.html"
    page.write_text("<!doctype html><title>Requirements</title>", encoding="utf-8")
    server = create_server(lambda: {"summary": {"specifications": 1}}, 0, page)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_http_serves_page_snapshot_and_rejects_writes_or_paths(tmp_path):
    server, thread = running_server(tmp_path)
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/") as response:
            assert response.status == 200
            assert b"Requirements" in response.read()
        with urllib.request.urlopen(base + "/app.css") as response:
            assert response.headers.get_content_type() == "text/css"
            assert b":focus-visible" in response.read()
        with urllib.request.urlopen(base + "/app.js") as response:
            assert response.headers.get_content_type() == "text/javascript"
            assert b"textContent" in response.read()
        with urllib.request.urlopen(base + "/api/state") as response:
            assert json.load(response)["summary"]["specifications"] == 1
        for path in ("/etc/passwd", "/api/state?path=/etc/passwd"):
            request = urllib.request.Request(base + path, method="POST" if path.startswith("/api") else "GET")
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            assert error.value.code in (404, 405)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_busy_port_has_actionable_error(tmp_path):
    server, thread = running_server(tmp_path)
    try:
        with pytest.raises(BrowserError, match="--port PORT"):
            create_server(lambda: {}, server.server_port, tmp_path / "page.html")
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
