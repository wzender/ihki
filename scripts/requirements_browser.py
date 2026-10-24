#!/usr/bin/env python3
"""Local, read-only browser for this repository's OpenSpec requirements."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit


HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PAGE = Path(__file__).with_name("requirements_browser.html")
STYLES = Path(__file__).with_name("requirements_browser.css")
SCRIPT = Path(__file__).with_name("requirements_browser.js")
Executor = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class BrowserError(RuntimeError):
    """An actionable setup or OpenSpec discovery error."""


def _execute(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, check=False)


class OpenSpecClient:
    def __init__(self, cwd: Path, executor: Executor = _execute):
        self.cwd = cwd.resolve()
        self.executor = executor

    def json(self, *arguments: str) -> dict:
        argv = ["openspec", *arguments]
        try:
            result = self.executor(argv, self.cwd)
        except FileNotFoundError as exc:
            raise BrowserError("OpenSpec is not installed or is not on PATH. Run `openspec --version` first.") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise BrowserError(f"`{' '.join(argv)}` failed: {detail or f'exit {result.returncode}'}")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise BrowserError(f"`{' '.join(argv)}` returned invalid JSON: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise BrowserError(f"`{' '.join(argv)}` returned JSON that is not an object")
        return value

    def root(self) -> Path:
        data = self.json("context", "--json")
        raw = data.get("root")
        path = raw.get("path") if isinstance(raw, dict) else None
        if not isinstance(path, str) or not path:
            raise BrowserError(
                "No OpenSpec root was found. Run this command inside an initialized project or run `openspec init`."
            )
        root = Path(path).resolve()
        if not (root / "openspec").is_dir():
            raise BrowserError(f"OpenSpec reported a root without an openspec directory: {root}")
        return root


def _inside(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath((str(root.resolve()), str(candidate.resolve()))) == str(root.resolve())
    except (OSError, ValueError):
        return False


def safe_read(root: Path, filename: str) -> str:
    path = Path(filename)
    if not path.is_absolute():
        path = root / path
    if not _inside(root, path):
        raise BrowserError(f"Refused artifact outside the OpenSpec root: {filename}")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BrowserError(f"Could not read {path.relative_to(root)}: {exc}") from exc


TASK = re.compile(r"^- \[([ xX])\]\s+(.*)$")
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def parse_markdown(text: str) -> list[dict]:
    """Extract readable structure without interpreting or trusting embedded HTML."""
    blocks: list[dict] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append({"type": "paragraph", "text": " ".join(paragraph).strip()})
            paragraph.clear()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            flush()
            continue
        if match := HEADING.match(line):
            flush()
            blocks.append({"type": "heading", "level": len(match.group(1)), "text": match.group(2).strip()})
        elif match := TASK.match(line):
            flush()
            blocks.append({"type": "task", "done": match.group(1).lower() == "x", "text": match.group(2).strip()})
        elif line.startswith(('- ', '* ')):
            flush()
            blocks.append({"type": "list", "text": line[2:].strip()})
        elif line.startswith("```"):
            flush()
            blocks.append({"type": "code-boundary", "text": line[3:].strip()})
        else:
            paragraph.append(line.strip())
    flush()
    return blocks


def _artifact_files(status: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    paths = status.get("artifactPaths")
    if not isinstance(paths, dict):
        return found
    for artifact_id, details in paths.items():
        if not isinstance(details, dict):
            continue
        existing = details.get("existingOutputPaths", [])
        if isinstance(existing, list):
            found.extend((str(artifact_id), item) for item in existing if isinstance(item, str))
    return found


def build_snapshot(client: OpenSpecClient, root: Path | None = None) -> dict:
    root = (root or client.root()).resolve()
    warnings: list[dict] = []
    specs: list[dict] = []
    changes: list[dict] = []

    inventory = client.json("list", "--specs", "--json")
    for item in inventory.get("specs", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            warnings.append({"file": "spec inventory", "message": "Ignored a malformed specification entry"})
            continue
        spec_id = item["id"]
        try:
            detail = client.json("show", spec_id, "--type", "spec", "--json")
            requirements = detail.get("requirements") if isinstance(detail.get("requirements"), list) else []
            specs.append({
                "id": spec_id,
                "title": detail.get("title") or spec_id,
                "purpose": detail.get("overview") or "",
                "requirementCount": len(requirements),
                "requirements": requirements,
            })
        except BrowserError as exc:
            warnings.append({"file": f"spec:{spec_id}", "message": str(exc)})

    change_inventory = client.json("list", "--json")
    for item in change_inventory.get("changes", []):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            warnings.append({"file": "change inventory", "message": "Ignored a malformed change entry"})
            continue
        name = item["name"]
        change = {"id": name, "completedTasks": item.get("completedTasks", 0),
                  "totalTasks": item.get("totalTasks", 0), "artifacts": []}
        try:
            status = client.json("status", "--change", name, "--json")
            change["schema"] = status.get("schemaName") or ""
            for artifact_id, filename in _artifact_files(status):
                try:
                    change["artifacts"].append({"id": artifact_id, "path": str(Path(filename).relative_to(root)),
                                                "blocks": parse_markdown(safe_read(root, filename))})
                except (BrowserError, ValueError) as exc:
                    warnings.append({"file": filename, "message": str(exc)})
        except BrowserError as exc:
            warnings.append({"file": f"change:{name}", "message": str(exc)})
        changes.append(change)

    return {
        "root": str(root),
        "summary": {"specifications": len(specs),
                    "requirements": sum(spec["requirementCount"] for spec in specs),
                    "changes": len(changes)},
        "specs": specs,
        "changes": changes,
        "warnings": warnings,
    }


def handler_for(snapshot: Callable[[], dict], page: Path = PAGE) -> type[BaseHTTPRequestHandler]:
    assets = {"/": (page, "text/html; charset=utf-8"),
              "/app.css": (STYLES, "text/css; charset=utf-8"),
              "/app.js": (SCRIPT, "text/javascript; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenSpecRequirements/1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            route = urlsplit(self.path).path
            if route in assets:
                filename, content_type = assets[route]
                try:
                    body = filename.read_bytes()
                except OSError as exc:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR,
                               json.dumps({"error": f"Could not load browser UI: {exc}"}).encode(),
                               "application/json; charset=utf-8")
                    return
                self._send(HTTPStatus.OK, body, content_type)
            elif route == "/api/state":
                try:
                    body = json.dumps(snapshot(), ensure_ascii=False).encode("utf-8")
                    self._send(HTTPStatus.OK, body, "application/json; charset=utf-8")
                except BrowserError as exc:
                    body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8")
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, body, "application/json; charset=utf-8")
            else:
                self._send(HTTPStatus.NOT_FOUND, b'{"error":"not found"}', "application/json; charset=utf-8")

        def do_POST(self) -> None:
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, b'{"error":"read only"}', "application/json; charset=utf-8")

        def log_message(self, format: str, *args: object) -> None:
            sys.stderr.write(f"requirements-browser: {format % args}\n")

    return Handler


def create_server(snapshot: Callable[[], dict], port: int, page: Path = PAGE) -> ThreadingHTTPServer:
    if not 0 <= port <= 65535:
        raise BrowserError("Port must be between 0 and 65535.")
    try:
        return ThreadingHTTPServer((HOST, port), handler_for(snapshot, page))
    except OSError as exc:
        raise BrowserError(f"Could not bind {HOST}:{port}: {exc}. Choose another port with `--port PORT`.") from exc


def parser() -> argparse.ArgumentParser:
    out = argparse.ArgumentParser(description="Browse this project's OpenSpec requirements locally")
    out.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"loopback port (default: {DEFAULT_PORT})")
    return out


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    client = OpenSpecClient(Path.cwd())
    try:
        root = client.root()
        server = create_server(lambda: build_snapshot(client, root), args.port)
    except BrowserError as exc:
        print(f"requirements-browser: {exc}", file=sys.stderr)
        return 2
    print(f"OpenSpec requirements: http://localhost:{server.server_port}")
    print(f"Reading: {root / 'openspec'}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
