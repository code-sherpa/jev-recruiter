"""Explicit provider routing for the shared state and choice question contract."""

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DecisionProvider:
    name: str
    url: str
    model: str
    key: str = field(repr=False)


def decision_provider():
    """Resolve exactly one provider, without credential or endpoint fallback."""
    name = os.environ.get("DECISION_PROVIDER", "typesafe").strip().lower()
    if name == "typesafe":
        url, default_model, prefix = "https://api.typesafe.ai/v1/systemone", "jev-latest", "TYPESAFE"
    elif name == "morph":
        url = os.environ.get("MORPH_API_URL", "https://api.morphllm.com/v1/singleshot").strip()
        default_model, prefix = "morph-systemone-v1", "MORPH"
    else:
        raise ValueError("DECISION_PROVIDER must be typesafe or morph.")
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Decision API URL must be an HTTP or HTTPS endpoint without credentials, query, or fragment.")
    key = os.environ.get(f"{prefix}_API_KEY", "").strip()
    if not key:
        raise ValueError(f"Configure {prefix}_API_KEY before requesting model decisions.")
    model = os.environ.get(f"{prefix}_MODEL", default_model).strip()
    if not model:
        raise ValueError(f"{prefix}_MODEL must not be empty.")
    return DecisionProvider(name, url, model, key)
