"""יאללה נדבר's live voice call (a trial, the switch tutor.eleven_agent; app/tutor_agent.py): the page gets a signed
address for the account's ElevenLabs agent, which is made once and pointed at this server; the agent's model is this
server, which runs the conversation's turn on what the student said and gives back the Arabic to say, and the page
fetches each turn to show it; a call from anyone without the token is refused."""
import io
import json

import pytest

from app import tutor_agent
from app.tutor_agent import AGENT_NAME, CHAT_MARK, LLM_PATH, AgentSetup, chat_id_of, llm_token, student_said
from test_tutor import FakeClient, TutorAuth, answer, bearer, client, start


class Answer(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeElevenLabs:
    """ElevenLabs' agents API, faked: the requests it got, and an account with no agent and no secret yet."""

    def __init__(self):
        self.seen, self.agents, self.secrets = [], [], []

    def __call__(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.seen.append((req.get_method(), req.full_url.replace(tutor_agent.API, ""), req.headers.get("Xi-api-key"), body))
        path = req.full_url.replace(tutor_agent.API, "")
        if path == "/v1/convai/secrets" and req.get_method() == "GET":
            out = {"secrets": self.secrets}
        elif path == "/v1/convai/secrets":
            self.secrets.append({"secret_id": "sec1", "name": body["name"]})
            out = {"secret_id": "sec1"}
        elif path.startswith("/v1/convai/agents?"):
            out = {"agents": self.agents}
        elif path == "/v1/convai/agents/create":
            self.agents.append({"agent_id": "agent_1", "name": body["name"]})
            out = {"agent_id": "agent_1"}
        elif path.startswith("/v1/convai/agents/"):
            out = {}
        elif path.startswith("/v1/convai/conversation/get-signed-url?"):
            out = {"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?agent_id=agent_1&conversation_signature=s"}
        else:
            raise AssertionError(path)
        return Answer(json.dumps(out).encode())


@pytest.fixture
def calling(monkeypatch):
    from app import main as main_module
    monkeypatch.setenv("FEATURE_PINS", "tutor.eleven_agent:admin=on")
    monkeypatch.setenv("ELEVEN_LABS_API_KEY", "el-key")
    monkeypatch.setenv("ELEVEN_LABS_TUTOR_VOICE_ID", "him")
    monkeypatch.setenv("ELEVEN_LABS_TUTOR_FEMALE_VOICE_ID", "her")
    monkeypatch.setenv("PUBLIC_URL", "https://ihki.example")
    fake = FakeElevenLabs()
    monkeypatch.setattr(main_module.tutor_agent.urllib.request, "urlopen", fake)
    return fake


def agent_says(c, chat_id, said, token=None, stream=True, extra=True):
    """The agent's request to this server, as ElevenLabs sends it: the call's history, and the page's extra body."""
    body = {"model": "x", "stream": stream,
            "messages": [{"role": "system", "content": CHAT_MARK + chat_id}, {"role": "assistant", "content": "كيف حالك؟"},
                         {"role": "user", "content": said}]}
    if extra:
        body["elevenlabs_extra_body"] = {"chat_id": chat_id}
    return c.post(LLM_PATH + "/chat/completions", json=body,
                  headers={"Authorization": f"Bearer {token or llm_token('el-key')}"})


def spoken(r):
    """The text of a streamed chat completion."""
    out = ""
    for line in r.text.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            out += json.loads(line[6:])["choices"][0]["delta"].get("content", "")
    return out


def test_a_call_starts_with_the_agent_made_once_and_pointed_at_this_server(calling):
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["live"] is True
        chat = start(c, scenario="l3").json()   # רובה: a woman's part
        r = c.post("/api/tutor/live", json={"id": chat["id"]}, headers=bearer("teacher"))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["signed_url"].startswith("wss://") and d["voice_id"] == "her" and d["chat_id"] == chat["id"]
        assert d["first_message"] == chat["message"]["ar"] and d["since"] == 0 and d["sdk"] == tutor_agent.SDK_URL
        assert d["keywords"] and len(d["keywords"]) <= 50
        made = next(b for m, p, _k, b in calling.seen if p == "/v1/convai/agents/create")
        llm = made["conversation_config"]["agent"]["prompt"]
        assert made["name"] == AGENT_NAME and llm["llm"] == "custom-llm" and llm["prompt"] == CHAT_MARK + "{{chat_id}}"
        assert llm["custom_llm"] == {"url": "https://ihki.example" + LLM_PATH, "api_key": {"secret_id": "sec1"}}
        assert made["conversation_config"]["tts"]["voice_id"] == "him"   # the agent's own; the call sets hers
        assert made["platform_settings"]["overrides"]["custom_llm_extra_body"] is True
        secret = next(b for m, p, _k, b in calling.seen if p == "/v1/convai/secrets" and m == "POST")
        assert secret["value"] == llm_token("el-key") and "el-key" not in json.dumps(made)   # the key itself is never given
        assert all(k == "el-key" for _m, _p, k, _b in calling.seen)
        n = len(calling.seen)
        assert c.post("/api/tutor/live", json={"id": chat["id"]}, headers=bearer("teacher")).status_code == 200
        assert [p.split("?")[0] for _m, p, _k, _b in calling.seen[n:]] == ["/v1/convai/conversation/get-signed-url"]


def test_the_agent_is_updated_rather_than_made_again(calling):
    calling.agents.append({"agent_id": "agent_9", "name": AGENT_NAME})
    with client(TutorAuth(), FakeClient()) as c:
        chat = start(c).json()
        assert c.post("/api/tutor/live", json={"id": chat["id"]}, headers=bearer("teacher")).status_code == 200
    methods = [(m, p) for m, p, _k, _b in calling.seen]
    assert ("PATCH", "/v1/convai/agents/agent_9") in methods and ("POST", "/v1/convai/agents/create") not in methods


def test_the_agents_model_is_the_tutor_and_the_page_gets_each_turn(calling):
    with client(TutorAuth(), FakeClient(script={2: answer(fix="عمري تلاتين", note="צריך לומר כך")})) as c:
        chat = start(c).json()
        r = agent_says(c, chat["id"], "انا مبسوط")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in r.headers   # a live stream is not held back to be compressed
        assert spoken(r) == "كيف حالك؟"   # the reply's Arabic, as the page's message has it
        got = c.get(f"/api/tutor/live/{chat['id']}?since=0", headers=bearer("teacher")).json()
        (turn,) = got["turns"]
        assert turn["heard"] == "אַנַא מַבְּסוּט" and turn["fix"]["ar"] == "عمري تلاتين" and turn["turn"] == 1
        assert turn["reply"]["he"] == "מה שלומך?" and not got["done"]
        assert c.get(f"/api/tutor/live/{chat['id']}?since=1", headers=bearer("teacher")).json()["turns"] == []
        # not streamed, and found by the prompt's id alone (no extra body)
        plain = agent_says(c, chat["id"], "شكرا", stream=False, extra=False).json()
        assert plain["choices"][0]["message"]["content"] == "كيف حالك؟"
        assert len(c.app.state.tutor.get_any(chat["id"]).live) == 2


def test_a_caller_without_the_token_is_refused(calling):
    with client(TutorAuth(), FakeClient()) as c:
        chat = start(c).json()
        assert agent_says(c, chat["id"], "مرحبا", token="nope").status_code == 401
        assert c.post(LLM_PATH + "/chat/completions", json={"messages": []}).status_code == 401
        assert agent_says(c, "0" * 8 + "-0000-0000-0000-" + "0" * 12, "مرحبا").status_code == 404
        assert c.app.state.tutor.get_any(chat["id"]).turns == 0


def test_another_account_cannot_follow_or_start_the_call(calling):
    with client(TutorAuth(), FakeClient()) as c:
        chat = start(c).json()
        c.app.state.tutor.get_any(chat["id"]).user_id = "someone-else"
        assert c.post("/api/tutor/live", json={"id": chat["id"]}, headers=bearer("teacher")).status_code == 410
        assert c.get(f"/api/tutor/live/{chat['id']}", headers=bearer("teacher")).status_code == 410


def test_with_the_switch_off_there_is_no_call(calling, monkeypatch):
    monkeypatch.delenv("FEATURE_PINS")
    with client(TutorAuth(), FakeClient()) as c:
        assert c.get("/api/tutor", headers=bearer("teacher")).json()["live"] is False
        chat = start(c).json()
        assert c.post("/api/tutor/live", json={"id": chat["id"]}, headers=bearer("teacher")).status_code == 403
    assert calling.seen == []


def test_the_model_failing_is_said_and_shown(calling, monkeypatch):
    from app.tutor import Tutor, TutorUnavailable

    def down(self, chat, text):
        raise TutorUnavailable("המורה עמוס כרגע. נסו שוב בעוד דקה.")
    with client(TutorAuth(), FakeClient()) as c:
        chat = start(c).json()
        monkeypatch.setattr(Tutor, "turn", down)
        r = agent_says(c, chat["id"], "مرحبا")
        assert spoken(r) == tutor_agent.SORRY
        (turn,) = c.get(f"/api/tutor/live/{chat['id']}", headers=bearer("teacher")).json()["turns"]
        assert turn["said"] == "مرحبا" and turn["error"]


def test_the_requests_parts():
    cid = "12345678-1234-1234-1234-123456789abc"
    assert chat_id_of({"elevenlabs_extra_body": {"chat_id": cid}}) == cid
    assert chat_id_of({"messages": [{"role": "system", "content": [{"type": "text", "text": CHAT_MARK + cid}]}]}) == cid
    assert chat_id_of({"elevenlabs_extra_body": {"chat_id": "../x"}}) == ""
    assert student_said({"messages": [{"role": "user", "content": " a  b "}, {"role": "assistant", "content": "x"}]}) == "a b"
    assert AgentSetup.config("u", "s", "v")["conversation_config"]["agent"]["language"] == "ar"
