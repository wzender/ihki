import copy

from app.math_skills import algebra9, problems


def test_the_algebra_graph_is_whole():
    graph = algebra9()
    assert problems(graph) == []
    assert len(graph["skills"]) == 56 and {u["id"] for u in graph["units"]} == {"f", "t", "n", "q", "p", "w"}
    # every unit opens somewhere a student can start: foundations need nothing
    assert all(s["prereqs"] == [] for s in graph["skills"] if s["id"] in ("f_neg", "f_frac"))


def test_a_broken_graph_is_named():
    graph = algebra9()
    skill = lambda g, sid: next(s for s in g["skills"] if s["id"] == sid)

    g = copy.deepcopy(graph)
    skill(g, "f_neg")["prereqs"] = ["f_ops"]   # f_ops needs f_neg
    assert problems(g)[0].startswith("cycle:")

    g = copy.deepcopy(graph)
    skill(g, "f_ops")["prereqs"].append("f_neg_typo")
    assert problems(g) == ["f_ops: unknown prerequisite f_neg_typo"]

    g = copy.deepcopy(graph)
    skill(g, "f_like")["prereqs"].append("f_neg")   # f_ops already brings it
    assert problems(g) == ["f_like: f_neg is implied by f_ops"]

    g = copy.deepcopy(graph)
    skill(g, "f_neg")["sample"]["choices"][1] = {"text": "$−13$", "correct": True}
    assert problems(g) == ["f_neg: needs exactly one correct choice"]

    g = copy.deepcopy(graph)
    g["mistakes"]["minus_minus"]["back"] = "t_ext"   # f_ops's mistake may not send it forward
    assert "f_ops: mistake minus_minus sends back to t_ext, which is not before it" in problems(g)


class FakeSolver:
    def __init__(self, readable=True):
        self.seen, self.readable = [], readable

    def solve(self, image, media_type):
        self.seen.append((image, media_type))
        return {"readable": self.readable, "problem": "$x² = 9$", "steps": [{"hint": "h", "work": "w"}],
                "answer": "$x = ±3$", "skill": "q_eq_simple", "note": ""}


def photo_app(monkeypatch, auth=None):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_scoring import emissions, scorer_for

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    c = TestClient(create_app(scorer_for(emissions(["w"])), auth=auth))
    c.__enter__()
    c.app.state.photo, c.app.state.photo_key = FakeSolver(), "sk-test"
    return c


def test_a_photographed_problem_is_solved(monkeypatch):
    c = photo_app(monkeypatch)
    send = lambda data=b"jpeg", kind="image/jpeg": c.post("/api/math/photo", files={"photo": ("p.jpg", data, kind)})
    r = send()
    assert r.status_code == 200 and r.json()["answer"] == "$x = ±3$" and r.json()["left"] is None   # open service: no limit
    assert c.app.state.photo.seen == [(b"jpeg", "image/jpeg")]
    assert send(kind="application/pdf").status_code == 415 and send(data=b"").status_code == 400
    assert send(data=b"x" * 5_000_001).status_code == 413 and len(c.app.state.photo.seen) == 1
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")   # no key: the feature says it is off
    monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
    assert send().status_code == 503
    c.__exit__(None, None, None)


def test_a_student_has_a_daily_number_of_photos(monkeypatch):
    import app.main as main

    class Auth:
        url, key, teachers, tutors, developers = "https://p.supabase.co", "k", frozenset(), frozenset(), frozenset()
        def user(self, token): return {"id": "u1", "email": "s@b.c"} if token == "s" else None
        def classes(self, fresh=False): return [{"id": 1, "name": "ט", "emails": ["s@b.c"], "nicknames": {}, "lessons": []}]
        def ranked(self, fresh=False): return frozenset()
        def __getattr__(self, name): return lambda *a, **k: []

    monkeypatch.setattr(main, "DAILY_PHOTOS", 2)
    c = photo_app(monkeypatch, Auth())
    send = lambda: c.post("/api/math/photo", files={"photo": ("p.jpg", b"jpeg", "image/jpeg")}, headers={"Authorization": "Bearer s"})
    assert c.post("/api/math/photo", files={"photo": ("p.jpg", b"jpeg", "image/jpeg")}).status_code == 401
    c.app.state.photo.readable = False   # a photo it could not read does not count
    assert send().json()["left"] == 2
    c.app.state.photo.readable = True
    assert [send().json()["left"], send().json()["left"]] == [1, 0] and send().status_code == 429
    c.__exit__(None, None, None)


def test_the_path_reads_the_graph():
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_scoring import emissions, scorer_for

    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:
        r = c.get("/api/math/skills")
        assert r.status_code == 200 and r.json() == algebra9()


def test_a_math_class_studies_math_units():
    from fastapi.testclient import TestClient
    from app.main import create_app, ALL_LESSONS
    from tests.test_scoring import emissions, scorer_for

    class Auth:
        url, key, teachers, tutors, developers = "https://p.supabase.co", "k", frozenset(), frozenset(), {"d@b.c"}
        users_ = {t: {"id": i, "email": f"{t}@b.c", "name": t} for t, i in (("s", "u1"), ("m", "u2"), ("d", "u3"))}
        def __init__(self): self.cls, self.saved = [], []
        def user(self, token): return self.users_.get(token)
        def users(self): return list(self.users_.values())
        def classes(self, fresh=False): return self.cls
        def access_for(self, user_id): return {"user_id": "u2", "roles": ["admin"], "subject": "מתמטיקה"} if user_id == "u2" else None
        def access_rows(self): return [self.access_for("u2")]
        def ranked(self, fresh=False): return frozenset()
        def save_class(self, *args, **kw): self.saved.append(args)
        def __getattr__(self, name): return lambda *a, **k: []

    tok = lambda t: {"Authorization": f"Bearer {t}"}
    with TestClient(create_app(scorer_for(emissions(["w"])), auth=Auth())) as c:
        auth = c.app.state.auth
        auth.cls = [{"id": 1, "name": "ט1", "emails": ["s@b.c"], "nicknames": {}, "lessons": ["q", "f"], "subject": "מתמטיקה"},
                    {"id": 2, "name": "ערבית", "emails": [], "nicknames": {}, "lessons": [], "subject": "ערבית"}]
        me = c.get("/api/me", headers=tok("s")).json()   # the student's path shows the class's units, in the path's order
        assert me["classes"][0]["lessons"] == ["f", "q"] and me["lessons"] == []
        listed = {k["name"]: k["lessons"] for k in c.get("/api/class", headers=tok("d")).json()["classes"]}
        assert listed == {"ט1": ["f", "q"], "ערבית": ALL_LESSONS}
        # the math admin opens a math class: units only, and no homework or daily videos even when sent
        new = lambda body, who="m": c.post("/api/classes", json={"name": "ט2", "emails": "", **body}, headers=tok(who))
        assert new({"lessons": [ALL_LESSONS[0]]}).status_code == 400
        assert new({"lessons": ["t", "f"], "homeworks": [{"topic": "x", "due": ""}], "dailies": [{"video": 9, "date": "2026-01-01"}]}).status_code == 200
        assert auth.saved[-1][4:] == (["f", "t"], [], None, "מתמטיקה")
        # a developer has no subject: the page says which; an edit keeps the class's own
        assert new({"lessons": ["n"], "subject": "מתמטיקה"}, "d").status_code == 200 and auth.saved[-1][4:] == (["n"], [], None, "מתמטיקה")
        assert new({"lessons": ["n"], "subject": "חשבון"}, "d").status_code == 400   # no subject: Arabic lessons
        assert c.put("/api/classes/1", json={"name": "ט1", "emails": "s@b.c", "lessons": ["w"]}, headers=tok("d")).status_code == 200
        assert auth.saved[-1][4] == ["w"]


def test_a_space_of_its_own_opens_on_its_subject(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from tests.test_scoring import emissions, scorer_for

    monkeypatch.setenv("HOME_SUBJECT", "math")   # mathly: "/" is the math app, and Arabic still has its own path
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:
        assert 'data-subject="math" data-home="math"' in c.get("/").text
        assert 'data-subject="arabic" data-home="math"' in c.get("/arabic").text
    monkeypatch.delenv("HOME_SUBJECT")
    with TestClient(create_app(scorer_for(emissions(["w"])))) as c:
        assert 'data-subject="arabic" data-home="arabic"' in c.get("/").text
