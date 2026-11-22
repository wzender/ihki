"""יאללה נדבר as a live voice call (a trial, behind the switch tutor.eleven_agent): an ElevenLabs agent listens to the
student and speaks, and our own tutor still writes every answer. The agent's "custom LLM" is this server
(POST /api/tutor/agent/v1/chat/completions, OpenAI's chat-completions shape, streamed): it runs the conversation's
Tutor.turn and gives the agent the reply's Arabic to say; the page fetches the rest of the turn (the Hebrew letters,
the translation, a correction, the summary) and shows it in the chat as a typed turn is shown.

The agent is made by the server in the account of ELEVEN_LABS_API_KEY, once, and kept up to date: one agent named
AGENT_NAME, whose model is this server, reached with a bearer token derived from the key (kept in the account's agent
secrets, never sent to a page). A page starts a call with a signed URL (GET get-signed-url), so the key stays here."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .tts import TTSUnavailable

log = logging.getLogger("ihki.tutor_agent")

API = "https://api.elevenlabs.io"
AGENT_NAME = "ihki-yalla-nedaber"
AGENT_TTS_MODEL = "eleven_v4_turbo"   # one of the models an agent may speak with (the workbook's fallback's too)
LLM_PATH = "/api/tutor/agent/v1"   # the base the agent is given; it posts to <base>/chat/completions
CHAT_MARK = "ihki-chat:"   # the agent's prompt is CHAT_MARK + the conversation's id (a dynamic variable)
CHAT_ID = re.compile(re.escape(CHAT_MARK) + r"([0-9a-f-]{36})")
SDK_URL = "https://cdn.jsdelivr.net/npm/@elevenlabs/client@1.32.1/dist/lib.iife.js"   # global ElevenLabsClient
SORRY = "عفوًا، ما فهمت منيح. عيد كمان مرة؟"   # what the agent says when the tutor did not answer


def llm_token(api_key: str) -> str:
    """The bearer token the agent sends to this server: derived from the ElevenLabs key, so nothing new is stored."""
    return hmac.new(api_key.encode(), b"ihki-tutor-agent-llm", hashlib.sha256).hexdigest()


def token_ok(api_key: str, header: str) -> bool:
    given = header[7:].strip() if header[:7].lower() == "bearer " else ""
    return bool(api_key and given) and hmac.compare_digest(given, llm_token(api_key))


def chat_id_of(body: dict) -> str:
    """The conversation a request of the agent is for: the page's custom_llm_extra_body (as ElevenLabs passes it on),
    else the id in the agent's prompt (the dynamic variable chat_id, as the system message has it)."""
    for extra in (body.get("elevenlabs_extra_body"), body.get("custom_llm_extra_body"), body):
        if isinstance(extra, dict) and isinstance(extra.get("chat_id"), str) and CHAT_ID.fullmatch(CHAT_MARK + extra["chat_id"]):
            return extra["chat_id"]
    for m in body.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "system":
            found = CHAT_ID.search(text_of(m.get("content")))
            if found:
                return found.group(1)
    return ""


def text_of(content) -> str:
    """A chat message's text: a string, or OpenAI's list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type", "text") == "text")
    return ""


def student_said(body: dict) -> str:
    """The student's latest words: the last user message of the request (ElevenLabs sends the call's whole history)."""
    for m in reversed(body.get("messages") or []):
        if isinstance(m, dict) and m.get("role") == "user":
            return " ".join(text_of(m.get("content")).split())
    return ""


def completion_chunks(text: str, model: str = "ihki-tutor"):
    """OpenAI's streamed chat completion of `text`, as server-sent events."""
    cid, now = f"chatcmpl-{int(time.time() * 1000)}", int(time.time())

    def event(delta: dict, finish: str | None = None) -> str:
        return "data: " + json.dumps({"id": cid, "object": "chat.completion.chunk", "created": now, "model": model,
                                      "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]},
                                     ensure_ascii=False) + "\n\n"
    yield event({"role": "assistant", "content": ""})
    if text:
        yield event({"content": text})
    yield event({}, "stop")
    yield "data: [DONE]\n\n"


def completion(text: str, model: str = "ihki-tutor") -> dict:
    """The same answer, not streamed (for a request with "stream": false)."""
    return {"id": f"chatcmpl-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
            "model": model, "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                         "finish_reason": "stop"}]}


class AgentSetup:
    """Finds or makes the account's agent, and keeps it pointing at this server with the chosen voice."""

    def __init__(self, opener=None):
        self._open = opener or urllib.request.urlopen
        self._lock = threading.Lock()
        self._ready: dict[tuple, str] = {}   # (key, llm url, voice) -> agent id, checked in this process

    def _call(self, key: str, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"xi-api-key": key, "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=20) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise TTSUnavailable(f"elevenlabs agents error {e.code}: {detail}", e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise TTSUnavailable(f"elevenlabs unreachable: {e}", transient=True) from None
        return json.loads(raw or b"{}")

    def secret_id(self, key: str) -> str:
        """The agent secret that holds this server's token (named by a hash of it, so a new key makes a new one)."""
        token = llm_token(key)
        name = f"ihki_tutor_llm_{hashlib.sha256(token.encode()).hexdigest()[:10]}"
        for s in self._call(key, "GET", "/v1/convai/secrets").get("secrets") or []:
            if s.get("name") == name:
                return s["secret_id"]
        return self._call(key, "POST", "/v1/convai/secrets", {"name": name, "value": token, "type": "new"})["secret_id"]

    @staticmethod
    def config(llm_url: str, secret_id: str, voice: str) -> dict:
        """The agent: Palestinian Arabic, this server as its model, and what a page may set for a call (its opening
        line, the voice of the part, the lesson's words to listen for, and the conversation's id for the model)."""
        return {
            "name": AGENT_NAME,
            "conversation_config": {
                "agent": {"first_message": "", "language": "ar",
                          "dynamic_variables": {"dynamic_variable_placeholders": {"chat_id": ""}},
                          "prompt": {"prompt": CHAT_MARK + "{{chat_id}}", "llm": "custom-llm",
                                     "custom_llm": {"url": llm_url, "api_key": {"secret_id": secret_id}}}},
                "tts": {"model_id": AGENT_TTS_MODEL, "voice_id": voice},
                "asr": {"quality": "high"},
            },
            "platform_settings": {"overrides": {
                "conversation_config_override": {"agent": {"first_message": True, "language": True},
                                                 "tts": {"voice_id": True}, "asr": {"keywords": True}},
                "custom_llm_extra_body": True}},
        }

    def ensure(self, key: str, llm_url: str, voice: str) -> str:
        """The agent's id, made or updated when this process has not yet checked it for this key, URL and voice."""
        want = (key, llm_url, voice)
        with self._lock:
            if want in self._ready:
                return self._ready[want]
            body = self.config(llm_url, self.secret_id(key), voice)
            found = self._call(key, "GET", "/v1/convai/agents?" + urllib.parse.urlencode({"search": AGENT_NAME}))
            agent = next((a["agent_id"] for a in found.get("agents") or [] if a.get("name") == AGENT_NAME), "")
            if agent:
                self._call(key, "PATCH", f"/v1/convai/agents/{agent}", body)
            else:
                agent = self._call(key, "POST", "/v1/convai/agents/create", body)["agent_id"]
                log.info("made the ElevenLabs agent for יאללה נדבר's live call: %s", agent)
            self._ready = {want: agent}
            return agent

    def signed_url(self, key: str, agent: str) -> str:
        """A one-call address for the page (the key stays on the server)."""
        return self._call(key, "GET", "/v1/convai/conversation/get-signed-url?"
                          + urllib.parse.urlencode({"agent_id": agent}))["signed_url"]
