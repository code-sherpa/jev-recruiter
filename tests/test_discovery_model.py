"""Offline checks that Jev title evidence gates discovery navigation."""

import pytest

from jev_ultrafast import discovery_model


def choice(value, options):
    return {"choice": value, "confidence": 1.0,
            "probabilities": {option: float(option == value) for option in options}}


@pytest.fixture
def provider(monkeypatch):
    calls, selections = [], {}
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test-key")
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)

    def post(url, key, body):
        calls.append((url, key, body))
        return {"model": "jev-latest", "usage": {"input_tokens": 321}, "answers": {
            name: choice(selections.get(name, "unknown" if name.endswith("status") else "none"), q["criteria"])
            for name, q in body["questions"].items()
        }}

    monkeypatch.setattr(discovery_model, "post_json", post)
    return calls, selections


def card(slug="person", **changes):
    return {"profile_url": f"https://www.linkedin.com/in/{slug}/", "label": "Observed person",
            "context": "Observed person\nField Marketing Manager at Example", **changes}


def test_batched_jev_choices_require_own_indexed_title(provider):
    calls, selections = provider
    selections.update(p1_status="relevant", p1_evidence="e2", p2_status="unrelated")
    profiles = [card(), card("engineer", context="Software Engineer")]
    results, metadata = discovery_model.screen_profiles("Field marketer in San Francisco", profiles)
    assert results[profiles[0]["profile_url"]]["status"] == "relevant"
    assert results[profiles[0]["profile_url"]]["quote"] == "Field Marketing Manager at Example"
    assert results[profiles[1]["profile_url"]]["status"] == "unrelated"
    assert results[profiles[1]["profile_url"]]["quote"] == ""
    assert len(calls) == 1
    url, key, body = calls[0]
    assert url == "https://api.typesafe.ai/v1/systemone"
    assert key == "offline-test-key"
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == {"p1_status", "p1_evidence", "p2_status", "p2_evidence"}
    assert "messages" not in body
    assert metadata["usage"] == {"input_tokens": 321}


def test_explicit_title_excludes_post_text_and_other_people(provider):
    calls, selections = provider
    selections.update(p1_status="unrelated")
    discovery_model.screen_profiles("Field marketer", [card(
        title="Founder at Example", context="Founder at Example\nMy friend is a Field Marketing Manager")])
    assert calls[0][2]["questions"]["p1_evidence"]["criteria"] == {
        "e1": "Founder at Example", "none": "No explicit relevant candidate title evidence.",
    }


def test_relevance_without_observed_title_is_unknown(provider):
    _, selections = provider
    selections.update(p1_status="relevant", p1_evidence="none")
    results, _ = discovery_model.screen_profiles("Field marketer", [card(context="")])
    assert results[card()["profile_url"]]["status"] == "unknown"
    assert results[card()["profile_url"]]["quote"] == ""


@pytest.mark.parametrize("status", ["unknown", "unrelated"])
def test_unused_evidence_head_cannot_create_eligible_profile(monkeypatch, provider, status):
    monkeypatch.setattr(discovery_model, "post_json", lambda *args: {
        "answers": {"p1_status": choice(status, discovery_model.STATUSES), "p1_evidence": None},
    })
    results, _ = discovery_model.screen_profiles("Field marketer", [card()])
    assert results[card()["profile_url"]]["status"] == status
    assert results[card()["profile_url"]]["quote"] == ""


def test_invented_evidence_target_rejected(provider):
    _, selections = provider
    selections.update(p1_status="relevant", p1_evidence="e999")
    with pytest.raises(ValueError, match="Invalid model choice response"):
        discovery_model.screen_profiles("Field marketer", [card()])


@pytest.mark.parametrize("profiles", [[], [card()] * 31, [card(), card()], [None],
                                     [card(context=None)], [card(title=1)],
                                     [card(profile_url="https://example.com/in/person/")],
                                     [card(profile_url="https://www.linkedin.com/in/person/?secret=1")],
                                     [card(profile_url="https://www.linkedin.com/in/person%2fother/")]])
def test_invalid_observations_do_not_call_provider(provider, profiles):
    calls, _ = provider
    with pytest.raises(ValueError):
        discovery_model.screen_profiles("Field marketer", profiles)
    assert not calls


def test_invalid_requirements_do_not_call_provider(provider):
    calls, _ = provider
    with pytest.raises(ValueError):
        discovery_model.screen_profiles("", [card()])
    assert not calls


def test_missing_key_does_not_call_provider(monkeypatch, provider):
    calls, _ = provider
    monkeypatch.delenv("TYPESAFE_API_KEY")
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        discovery_model.screen_profiles("Field marketer", [card()])
    assert not calls


def test_title_snippets_are_bounded_and_verbatim():
    profile = card(context="\n".join(f"Observed professional headline {i}" for i in range(30)))
    snippets = discovery_model.title_excerpts(profile)
    assert len(snippets) == 12
    assert all(quote in profile["context"] for quote in snippets.values())


def test_all_heads_prohibit_post_inference_and_protected_traits(provider):
    calls, _ = provider
    discovery_model.screen_profiles("Field marketer", [card()])
    for question in calls[0][2]["questions"].values():
        rules = question["instructions"]["rules"]
        assert "protected traits" in rules
        assert "untrusted data" in rules
        assert "Talking about a function in a post does not" in rules
        assert "supplied job requirements" in rules
        assert "explicitly accepted alternative" in rules


def test_engineering_roles_use_requested_requirements_without_marketing_bias(provider):
    calls, selections = provider
    selections.update(p1_status="relevant", p1_evidence="e1", p2_status="relevant", p2_evidence="e1")
    requirements = "Forward deployed engineer or solutions engineer\n3 to 5 years relevant experience"
    profiles = [card("forward", context="Forward Deployed Engineer"),
                card("solutions", context="Solutions Engineer")]
    results, _ = discovery_model.screen_profiles(requirements, profiles)
    assert all(result["status"] == "relevant" for result in results.values())
    for question in calls[0][2]["questions"].values():
        assert "[Required] Forward deployed engineer or solutions engineer" in question["instructions"]["requirements"]
        assert "marketing" not in question["instructions"]["rules"]
