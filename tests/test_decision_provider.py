"""Offline routing, authentication, identical payload, and fail closed contracts."""

import json

import httpx
import pytest

from jev_ultrafast import discovery_model, model, recruiting_model
from jev_ultrafast.decision_provider import decision_provider
from jev_ultrafast.recruiter import Recruiter


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for name in ("DECISION_PROVIDER", "TYPESAFE_MODEL", "MORPH_MODEL", "MORPH_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-offline-secret")
    monkeypatch.setenv("MORPH_API_KEY", "morph-offline-secret")


def invoke(kind):
    if kind == "navigation":
        return model.choose({"url": "https://example.test", "title": "Observed page", "text": "Observed text",
                             "actions": [{"id": "wait", "kind": "wait", "label": "Wait"}]}, "Read", [])
    if kind == "screen":
        return discovery_model.screen_profiles("Solutions engineer", [{
            "profile_url": "https://www.linkedin.com/in/example/", "label": "Example",
            "context": "Solutions engineer"}])
    return recruiting_model.assess(["Solutions engineer"], ["Observed experience"], "observed profile")


def response(body):
    return {"model": body["model"], "answers": {
        name: {"choice": choice, "confidence": 1.0,
               "probabilities": {option: float(option == choice) for option in q["criteria"]}}
        for name, q in body["questions"].items()
        for choice in ["DONE" if name == "operation" else "unknown" if name.endswith("status") else "none"]
    }}


@pytest.mark.parametrize("kind", ["navigation", "screen", "assessment"])
def test_all_calls_route_with_same_choice_contract(monkeypatch, kind):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=response(json.loads(request.content)))

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(model, "CLIENT", client)
        invoke(kind)
        monkeypatch.setenv("DECISION_PROVIDER", "morph")
        invoke(kind)
    assert len(requests) == 2
    first, second = requests
    assert str(first.url) == "https://api.typesafe.ai/v1/systemone"
    assert first.headers["Authorization"] == "Bearer typesafe-offline-secret"
    assert str(second.url) == "https://api.morphllm.com/v1/singleshot"
    assert second.headers["Authorization"] == "Bearer morph-offline-secret"
    one, two = json.loads(first.content), json.loads(second.content)
    assert one.pop("model") == "jev-latest"
    assert two.pop("model") == "morph-systemone-v1"
    assert one == two


@pytest.mark.parametrize("kind", ["navigation", "screen", "assessment"])
@pytest.mark.parametrize("failure", ["http", "json", "answers", "choice"])
def test_morph_failure_stops_without_typesafe_fallback(monkeypatch, kind, failure):
    monkeypatch.setenv("DECISION_PROVIDER", "morph")
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "http":
            return httpx.Response(404, text="private provider response")
        if failure == "json":
            return httpx.Response(200, text="private provider response")
        if failure == "answers":
            return httpx.Response(200, json={"answers": []})
        result = response(json.loads(request.content))
        for answer in result["answers"].values():
            answer["choice"] = "invented"
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(model, "CLIENT", client)
        with pytest.raises((ValueError, RuntimeError)) as exc:
            invoke(kind)
    assert len(requests) == 1
    assert requests[0].url.host == "api.morphllm.com"
    assert "private provider response" not in str(exc.value)


def test_missing_selected_key_cannot_use_other_provider(monkeypatch):
    monkeypatch.setenv("DECISION_PROVIDER", "morph")
    monkeypatch.delenv("MORPH_API_KEY")
    with pytest.raises(ValueError, match="MORPH_API_KEY"):
        decision_provider()


def test_explicit_morph_endpoint_and_model(monkeypatch):
    monkeypatch.setenv("DECISION_PROVIDER", "morph")
    monkeypatch.setenv("MORPH_API_URL", "http://127.0.0.1:8080/v1/singleshot")
    monkeypatch.setenv("MORPH_MODEL", "configured-model")
    provider = decision_provider()
    assert provider.url == "http://127.0.0.1:8080/v1/singleshot"
    assert provider.model == "configured-model"
    assert provider.key not in repr(provider)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:secret@example.test/api",
                                 "https://example.test/api?key=secret", "https://example.test/api#fragment"])
def test_invalid_endpoint_is_rejected(monkeypatch, url):
    monkeypatch.setenv("DECISION_PROVIDER", "morph")
    monkeypatch.setenv("MORPH_API_URL", url)
    with pytest.raises(ValueError, match="Decision API URL"):
        decision_provider()


def test_invalid_provider_rejected_without_guessing(monkeypatch):
    monkeypatch.setenv("DECISION_PROVIDER", "typo")
    with pytest.raises(ValueError, match="DECISION_PROVIDER"):
        decision_provider()


def test_morph_run_metadata_and_labels_do_not_expose_credential(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DECISION_PROVIDER", "morph")
    monkeypatch.delenv("TYPESAFE_API_KEY")
    run = Recruiter("Solutions engineer")
    run._log("Requested Jev browser decision")
    state = run.snapshot()
    assert state["provider"] == "morph"
    assert state["provider_label"] == "Morph"
    assert state["model"] == "morph-systemone-v1"
    assert state["history"][0]["action"] == "Requested Morph browser decision"
    assert "morph-offline-secret" not in run.path.read_text()
