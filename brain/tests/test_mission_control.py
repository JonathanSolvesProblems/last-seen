"""Mission control's API: the coordinator's answer is the only thing that confirms anyone."""

from __future__ import annotations

import json

from mission_control.app import App
from mission_control.store import MemoryStore

AUTH = {"Authorization": "Bearer t0k"}


def call(app, method, path, body=None, headers=AUTH, query=None):
    code, ctype, out = app.handle(method, path, headers, json.dumps(body or {}).encode(), query or {})
    return code, (json.loads(out) if ctype == "application/json" else out)


def test_every_api_call_needs_the_token():
    app = App(MemoryStore(), "t0k")
    code, body = call(app, "GET", "/api/missions", headers={})
    assert code == 401 and body["errorCategory"] == "permission"
    assert call(app, "GET", "/", headers={})[0] == 401
    assert call(app, "GET", "/", headers={}, query={"token": "t0k"})[0] == 200


def test_question_answer_round_trip():
    app = App(MemoryStore(), "t0k")
    code, q = call(app, "POST", "/api/missions/m1/questions", {"candidate": "P1", "match": 0.9, "message": "blue top"})
    assert code == 201
    assert call(app, "GET", f"/api/missions/m1/questions/{q['id']}")[1]["decision"] is None
    assert call(app, "POST", f"/api/missions/m1/questions/{q['id']}/answer", {"decision": "confirmed"})[0] == 200
    assert call(app, "GET", f"/api/missions/m1/questions/{q['id']}")[1]["decision"] == "confirmed"


def test_an_answer_cannot_be_changed():
    """Two taps, or a second person, cannot flip a decision after the aircraft acted on it."""
    app = App(MemoryStore(), "t0k")
    _, q = call(app, "POST", "/api/missions/m1/questions", {"candidate": "P1", "match": 0.9, "message": "x"})
    call(app, "POST", f"/api/missions/m1/questions/{q['id']}/answer", {"decision": "confirmed"})
    code, body = call(app, "POST", f"/api/missions/m1/questions/{q['id']}/answer", {"decision": "rejected"})
    assert code == 409 and body["errorCategory"] == "business"


def test_only_two_decisions_exist():
    app = App(MemoryStore(), "t0k")
    _, q = call(app, "POST", "/api/missions/m1/questions", {"candidate": "P1", "match": 0.9, "message": "x"})
    code, body = call(app, "POST", f"/api/missions/m1/questions/{q['id']}/answer", {"decision": "probably"})
    assert code == 400 and body["errorCategory"] == "validation"


def test_recall_reaches_the_field_on_the_next_state_post():
    app = App(MemoryStore(), "t0k")
    assert call(app, "POST", "/api/missions/m1/state", {"battery": 80})[1]["recall"] is None
    call(app, "POST", "/api/missions/m1/recall")
    assert call(app, "POST", "/api/missions/m1/state", {"battery": 79})[1]["recall"] is not None


def test_reference_photo_is_stored_once_and_served_to_the_page():
    app = App(MemoryStore(), "t0k")
    call(app, "POST", "/api/missions/m1/state", {"battery": 80, "reference_jpeg_b64": "QUJD"})
    m = call(app, "GET", "/api/missions/m1")[1]
    assert m["reference_data"] == "QUJD"
    assert "reference_jpeg_b64" not in (m["state"] or {})
