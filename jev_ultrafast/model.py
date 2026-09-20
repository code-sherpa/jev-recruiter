"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import logging
import math
import os
import time

import httpx

from .decision_provider import decision_provider
from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)
LOGGER = logging.getLogger(__name__)

MAX_ATTEMPTS = 10
BASE_DELAY_SECONDS = 0.5
MAX_DELAY_SECONDS = 20
RETRYABLE_STATUSES = {429, 500, 502, 503, 504, 529}


def post_json(url, key, body):
    # Only the unchanged model request is repeated here. Browser mutations live outside this function.
    for attempt in range(MAX_ATTEMPTS):
        last_attempt = attempt == MAX_ATTEMPTS - 1
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            if last_attempt:
                LOGGER.error("Model request failed after %s attempts (%s); giving up",
                             MAX_ATTEMPTS, type(exc).__name__)
                raise RuntimeError("Model connection failed; no action executed.") from None
            delay = min(BASE_DELAY_SECONDS * 2**attempt, MAX_DELAY_SECONDS)
            LOGGER.warning("Model request error (%s) on attempt %s of %s; retrying in %.1fs",
                           type(exc).__name__, attempt + 1, MAX_ATTEMPTS, delay)
            time.sleep(delay)
            continue
        if response.status_code in RETRYABLE_STATUSES and not last_attempt:
            delay = min(BASE_DELAY_SECONDS * 2**attempt, MAX_DELAY_SECONDS)
            LOGGER.warning("Model provider returned HTTP %s on attempt %s of %s; retrying in %.1fs",
                           response.status_code, attempt + 1, MAX_ATTEMPTS, delay)
            time.sleep(delay)
            continue
        if response.is_error:
            LOGGER.error("Model provider returned HTTP %s after %s attempt(s); no action executed",
                         response.status_code, attempt + 1)
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        if attempt:
            LOGGER.info("Model request succeeded on attempt %s of %s", attempt + 1, MAX_ATTEMPTS)
        try:
            return response.json()
        except ValueError:
            raise ValueError("Model provider returned malformed JSON; no action executed.") from None
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    reason = None
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        if answer["choice"] not in ids:
            reason = "choice is not an offered option"
        elif set(probabilities) != set(ids):
            reason = "probability options differ from offered options"
        elif not all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers):
            reason = "probabilities or confidence are invalid"
        elif abs(sum(probabilities.values()) - 1) >= 0.02:
            reason = "probabilities do not sum to one"
        elif probabilities[answer["choice"]] < max(probabilities.values()) - 1e-6:
            reason = "choice differs from the highest probability option"
    except (KeyError, TypeError, ValueError, AttributeError):
        reason = "required choice fields are missing or malformed"
    if reason:
        # Report a code owned diagnosis without echoing arbitrary provider data.
        raise ValueError(f"Invalid model choice response: {reason}; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    provider = decision_provider()
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": provider.model,
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json(provider.url, provider.key, body)
    if not isinstance(result, dict) or not isinstance(result.get("answers"), dict):
        raise ValueError("Model provider returned no valid decision answers; no action executed.")
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result.get("model", provider.model),
        "provider": provider.name,
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
