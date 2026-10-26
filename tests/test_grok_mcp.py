"""The Grok connector has a deliberately tiny, independently authenticated scope."""
import asyncio
from types import SimpleNamespace

import httpx

from app.auth import AuthUnavailable
from app.grok_mcp import ApprovalStore, BearerAuthApp, MutationLimiter, build
from app.main import create_app, video_link
from tests.test_scoring import emissions, scorer_for


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class LibraryAuth:
    url = "https://example.supabase.co"
    key = "publishable"
    teachers = frozenset({"teacher@example.com"})
    tutors = frozenset()

    def __init__(self):
        self.videos = []
        self.events = []

    def level_videos(self, table, fresh=False):
        assert table == "library"
        return list(self.videos)

    def add_level_video(self, table, level, title, url):
        self.videos.append({"id": len(self.videos) + 1, "level": level, "title": title, "url": url})

    def rename_level_video(self, table, video_id, title):
        next(video for video in self.videos if video["id"] == video_id)["title"] = title

    def log_event(self, kind, actor, label, details):
        self.events.append((kind, actor, label, details))

    def user(self, token):
        return {"id": "teacher-1", "email": "teacher@example.com"} if token == "teacher-session" else None

    def classes(self, fresh=False):
        return []

    def ranked(self, fresh=False):
        return []


class UnavailableAuth(LibraryAuth):
    def level_videos(self, table, fresh=False):
        raise AuthUnavailable("offline")


def connector(auth=None, clock=None):
    clock = clock or Clock()
    host = SimpleNamespace(state=SimpleNamespace(auth=auth, video_link=video_link))
    server, protected, _ = build(host, approvals=ApprovalStore(clock), limiter=MutationLimiter(clock))
    return server, protected


def call(server, name, arguments=None):
    answer = asyncio.run(server.call_tool(name, arguments or {}))
    return answer.structured_content


def test_exactly_four_narrow_tools_are_discoverable():
    server, _ = connector(LibraryAuth())
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == [
        "list_library_videos", "prepare_library_video", "publish_library_video", "rename_library_video"]
    assert all(tool.output_schema for tool in tools)
    assert not any(word in tool.name for tool in tools for word in ("delete", "class", "teacher", "user", "sql"))


def test_list_and_rename_return_only_library_metadata():
    auth = LibraryAuth()
    auth.videos = [{"id": 7, "level": "", "title": "ישן", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                    "student_email": "must-not-leak@example.com"}]
    server, _ = connector(auth)
    listed = call(server, "list_library_videos")
    assert listed == {"ok": True, "videos": [{"id": 7, "title": "ישן",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "position": 1}]}
    assert call(server, "rename_library_video", {"video_id": 7, "title": "  שם   חדש "}) == {
        "ok": True, "video_id": 7, "title": "שם חדש"}
    assert auth.videos[0]["url"].endswith("dQw4w9WgXcQ")
    assert call(server, "rename_library_video", {"video_id": 99, "title": "x"})["ok"] is False
    assert call(server, "rename_library_video", {"video_id": 7, "title": ""})["ok"] is False
    assert auth.events and all("token" not in str(event).lower() for event in auth.events)


def test_prepare_publish_is_expiring_single_use_and_rechecks_duplicates():
    clock, auth = Clock(), LibraryAuth()
    server, _ = connector(auth, clock)
    prepared = call(server, "prepare_library_video", {
        "title": "  ברכות   בערבית ", "url": "https://youtu.be/dQw4w9WgXcQ?t=10"})
    assert prepared["ok"] and not prepared["published"]
    assert prepared["title"] == "ברכות בערבית"
    assert prepared["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    published = call(server, "publish_library_video", {"approval_id": prepared["approval_id"]})
    assert published["ok"] and published["published"] and len(auth.videos) == 1
    assert call(server, "publish_library_video", {"approval_id": prepared["approval_id"]})["ok"] is False
    assert call(server, "prepare_library_video", {
        "title": "כפול", "url": "https://youtu.be/dQw4w9WgXcQ"})["ok"] is False

    other = call(server, "prepare_library_video", {"title": "אחר", "url": "https://vimeo.com/1233611297/d46ac373c8"})
    auth.videos.append({"id": 2, "level": "", "title": "נוסף במקביל", "url": other["url"]})
    assert call(server, "publish_library_video", {"approval_id": other["approval_id"]})["ok"] is False

    expiring = call(server, "prepare_library_video", {"title": "פג", "url": "https://youtu.be/abc12345678"})
    clock.now += 601
    assert call(server, "publish_library_video", {"approval_id": expiring["approval_id"]})["ok"] is False


def test_invalid_and_unavailable_operations_are_safe():
    server, _ = connector(LibraryAuth())
    assert call(server, "prepare_library_video", {"title": "x", "url": "https://example.com/x"})["ok"] is False
    assert call(server, "prepare_library_video", {"title": "", "url": "https://youtu.be/dQw4w9WgXcQ"})["ok"] is False
    unavailable, _ = connector(UnavailableAuth())
    assert call(unavailable, "list_library_videos")["ok"] is False
    assert call(unavailable, "prepare_library_video", {
        "title": "x", "url": "https://youtu.be/dQw4w9WgXcQ"})["ok"] is False


def asgi_request(app, authorization=None):
    sent = []
    headers = [] if authorization is None else [(b"authorization", authorization.encode())]
    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def test_bearer_auth_is_independent_and_does_not_disclose_tokens():
    async def accepted(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    disabled = BearerAuthApp(accepted, token=lambda: "")
    assert asgi_request(disabled)[0]["status"] == 503
    protected = BearerAuthApp(accepted, token=lambda: "correct-secret")
    missing = asgi_request(protected)
    wrong = asgi_request(protected, "Bearer wrong-secret")
    good = asgi_request(protected, "Bearer correct-secret")
    assert missing[0]["status"] == wrong[0]["status"] == 401
    assert good[0]["status"] == 204
    assert b"correct-secret" not in b"".join(message.get("body", b"") for message in missing + wrong + good)


def test_streamable_http_initializes_and_discovers_tools(monkeypatch):
    monkeypatch.setenv("GROK_LIBRARY_TOKEN", "disposable-test-token")
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    app = create_app(scorer_for(emissions(["w"])))
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                accept = {"Accept": "application/json, text/event-stream"}
                assert (await client.post("/mcp/", json=initialize, headers=accept)).status_code == 401
                headers = {**accept, "Authorization": "Bearer disposable-test-token"}
                initialized = await client.post("/mcp/", json=initialize, headers=headers)
                discovered = await client.post("/mcp/", json={
                    "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=headers)
                return initialized, discovered

    initialized, discovered = asyncio.run(request())
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "ihki-digital-library"
    assert [tool["name"] for tool in discovered.json()["result"]["tools"]] == [
        "list_library_videos", "prepare_library_video", "publish_library_video", "rename_library_video"]


def test_existing_library_api_is_compatible_and_mcp_token_has_no_api_authority(monkeypatch):
    monkeypatch.setenv("GROK_LIBRARY_TOKEN", "mcp-only-token")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    auth = LibraryAuth()
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                mcp = {"Authorization": "Bearer mcp-only-token"}
                teacher = {"Authorization": "Bearer teacher-session"}
                assert (await client.get("/api/library", headers=mcp)).status_code == 401
                first = await client.post("/api/library", headers=teacher, json={
                    "title": "  ראשון  ", "url": "https://youtu.be/dQw4w9WgXcQ?t=3"})
                second = await client.post("/api/library", headers=teacher, json={
                    "title": "שני", "url": "https://youtu.be/dQw4w9WgXcQ"})
                renamed = await client.put("/api/library/1", headers=teacher, json={"title": "  חדש  "})
                listed = await client.get("/api/library", headers=teacher)
                return first, second, renamed, listed

    first, second, renamed, listed = asyncio.run(request())
    assert first.status_code == second.status_code == renamed.status_code == 200
    assert [(row["id"], row["title"], row["url"]) for row in listed.json()["library"]] == [
        (1, "חדש", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        (2, "שני", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")]


def test_streamable_http_manages_library_end_to_end_and_browser_still_opens(monkeypatch):
    monkeypatch.setenv("GROK_LIBRARY_TOKEN", "disposable-e2e-token")
    monkeypatch.setenv("SAWTAK_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    auth = LibraryAuth()
    app = create_app(scorer_for(emissions(["w"])), auth=auth)

    async def request():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                headers = {"Authorization": "Bearer disposable-e2e-token",
                           "Accept": "application/json, text/event-stream"}
                next_id = 1

                async def tool(name, arguments=None):
                    nonlocal next_id
                    response = await client.post("/mcp/", headers=headers, json={
                        "jsonrpc": "2.0", "id": next_id, "method": "tools/call",
                        "params": {"name": name, "arguments": arguments or {}}})
                    next_id += 1
                    assert response.status_code == 200, response.text
                    return response.json()["result"]["structuredContent"]

                prepared = await tool("prepare_library_video", {
                    "title": "  דרך גרוק ", "url": "https://youtu.be/dQw4w9WgXcQ?t=4"})
                published = await tool("publish_library_video", {"approval_id": prepared["approval_id"]})
                listed = await tool("list_library_videos")
                renamed = await tool("rename_library_video", {"video_id": published["video_id"], "title": "שם חדש"})
                page = await client.get("/", headers={"Authorization": "Bearer teacher-session"})
                return prepared, published, listed, renamed, page

    prepared, published, listed, renamed, page = asyncio.run(request())
    assert prepared["ok"] and not prepared["published"]
    assert published["ok"] and published["published"]
    assert listed["videos"] == [{"id": 1, "title": "דרך גרוק",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "position": 1}]
    assert renamed == {"ok": True, "video_id": 1, "title": "שם חדש"}
    assert page.status_code == 200 and "ניהול" in page.text


def test_mutations_are_bounded_but_listing_remains_available():
    auth, clock = LibraryAuth(), Clock()
    server, _ = connector(auth, clock)
    for number in range(20):
        result = call(server, "prepare_library_video", {
            "title": str(number), "url": f"https://youtu.be/{number:011d}"})
        assert result["ok"]
    refused = call(server, "prepare_library_video", {"title": "later", "url": "https://youtu.be/abcdefghijk"})
    assert refused["ok"] is False and "דקה" in refused["error"]
    assert call(server, "list_library_videos")["ok"] is True
