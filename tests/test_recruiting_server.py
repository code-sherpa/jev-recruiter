"""Exercise the real local HTTP boundary without Chrome or paid model calls."""

import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import demo


@pytest.fixture
def server(monkeypatch):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), demo.Handler)
    port = httpd.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(demo, "PORT", port)
    monkeypatch.setattr(demo, "ORIGIN", origin)
    monkeypatch.setattr(demo, "RECRUITER", None)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=origin) as client:
        yield client
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def test_recruiting_start_requires_local_token_and_origin(server, monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(demo, "Recruiter", constructor)
    path = "/api/recruiting/start"
    assert server.post(path, json={}).status_code == 403
    assert server.post(path, json={}, headers={
        "X-Demo-Token": demo.TOKEN, "Origin": "https://example.com",
    }).status_code == 403
    assert server.get("/api/recruiting/state", headers={"Host": "example.com"}).status_code == 403
    constructor.assert_not_called()


def test_bad_body_and_unknown_command(server):
    headers = {"X-Demo-Token": demo.TOKEN}
    for body in ([], None, "requirements"):
        response = server.post("/api/recruiting/start", content=json.dumps(body), headers=headers)
        assert response.status_code == 400
    assert server.post("/api/recruiting/nope", json={}, headers=headers).status_code == 400


def test_failed_start_preserves_existing_results(server, monkeypatch):
    previous = Mock()
    monkeypatch.setattr(demo, "RECRUITER", previous)
    monkeypatch.setattr(demo, "Recruiter", Mock(side_effect=ValueError("Missing model configuration")))
    response = server.post("/api/recruiting/start", json={"requirements": "Field marketing"},
                           headers={"X-Demo-Token": demo.TOKEN})
    assert response.status_code == 400
    assert "Missing model" in response.json()["error"]
    assert demo.RECRUITER is previous
    previous.close.assert_not_called()


def test_routes_and_idle_state(server):
    root = server.get("/")
    assert root.status_code == 200
    assert demo.TOKEN in root.text
    assert "__TOKEN__" not in root.text
    assert server.get("/recruiter.js").status_code == 200
    assert server.get("/recruiter.css").status_code == 200
    assert server.get("/demo").status_code == 200
    assert server.get("/api/recruiting/state").json()["status"] == "idle"
