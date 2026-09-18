"""Bounded, read only LinkedIn sourcing with quoted evidence and human review."""

import copy
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .browser import Browser
from .model import choose
from .recruiting_model import assess, parse_criteria

FEED_URL = "https://www.linkedin.com/feed/"


def profile_url(value):
    """Accept only a canonical profile route from an observed HTTPS LinkedIn anchor."""
    if not isinstance(value, str):
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc != "www.linkedin.com":
        return None
    if not re.fullmatch(r"/in/[A-Za-z0-9_%~-]+/?", parsed.path):
        return None
    # Reject encoded path separators and control characters, even inside a slug.
    if re.search(r"%(?:2f|5c|0[0-9a-f]|1[0-9a-f]|7f)", parsed.path, re.I):
        return None
    return "https://www.linkedin.com" + parsed.path.rstrip("/") + "/"


def validate_assessment(output, criteria, evidence):
    """Require full rubric coverage and verbatim support for every nonunknown finding."""
    items = output.get("criteria") if isinstance(output, dict) else None
    if not isinstance(items, list) or len(items) != len(criteria):
        raise ValueError("Assessment did not cover every job criterion.")
    validated = []
    for expected, item in zip(criteria, items, strict=True):
        if not isinstance(item, dict) or item.get("criterion") != expected:
            raise ValueError("Assessment changed the job criteria.")
        status, quote = item.get("status"), item.get("quote", "")
        if status not in {"met", "not_met", "unknown"} or not isinstance(quote, str):
            raise ValueError("Assessment contained an invalid evidence finding.")
        if status != "unknown" and (not quote.strip() or not any(quote in text for text in evidence)):
            # Unsupported conclusions cannot become screening decisions.
            status, quote = "unknown", ""
        elif quote and not any(quote in text for text in evidence):
            quote = ""
        validated.append({"criterion": expected, "status": status, "quote": quote})
    statuses = {item["status"] for item in validated if not item["criterion"].startswith("[Preferred] ")}
    recommendation = "needs_review"
    if statuses == {"met"}:
        recommendation = "potential_match"
    elif "not_met" in statuses:
        recommendation = "not_a_match"
    # Avoid unverified freeform model claims outside the validated evidence fields.
    summary = {
        "potential_match": "Visible evidence supports all required criteria. Human review is required.",
        "needs_review": "Some qualifications could not be verified in the visible profile excerpts.",
        "not_a_match": "Visible evidence conflicts with at least one required criterion. Human review is required.",
    }[recommendation]
    return {"criteria": validated, "recommendation": recommendation, "summary": summary}


class Recruiter:
    def __init__(self, requirements, max_profiles=10, max_scrolls=10):
        if not isinstance(requirements, str) or not 10 <= len(requirements.strip()) <= 12000:
            raise ValueError("Enter job requirements between 10 and 12000 characters.")
        for label, value in (("max_profiles", max_profiles), ("max_scrolls", max_scrolls)):
            limit = 50 if label == "max_profiles" else 100
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f"{label} must be an integer from 1 to {limit}.")
        if not os.environ.get("TYPESAFE_API_KEY", "").strip():
            raise ValueError("Configure TYPESAFE_API_KEY before starting recruiting.")
        self.run_id = uuid4().hex
        self.requirements = requirements.strip()
        self.max_profiles, self.max_scrolls = max_profiles, max_scrolls
        self.status, self.message, self.error = "ready", "Ready to read the LinkedIn feed.", None
        self.criteria = parse_criteria(self.requirements)
        self.candidates, self.history = [], []
        self.queue, self.seen, self.discoveries = [], set(), []
        self.feed, self.profile, self.current = None, None, None
        self.page = None
        self.feed_scrolls = self.model_calls = 0
        self.profile_scrolls = 0
        self.decision = None
        self.idle_steps = 0
        self.path = Path("artifacts/recruiting") / f"{self.run_id}.json"
        self._save()

    def snapshot(self):
        return copy.deepcopy({
            "run_id": self.run_id, "requirements": self.requirements, "status": self.status,
            "message": self.message, "error": self.error, "criteria": self.criteria,
            "max_profiles": self.max_profiles, "max_scrolls": self.max_scrolls,
            "counts": {"discovered": len(self.seen), "reviewed": len(self.candidates),
                       "queued": len(self.queue), "feed_scrolls": self.feed_scrolls,
                       "model_calls": self.model_calls},
            "candidates": self.candidates, "history": self.history,
            "discoveries": self.discoveries,
            "decision": self.decision, "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
            "limitations": "Only three visible profile screens are read. Collapsed sections are not expanded. "
                            "Nearby recommendations may appear in excerpts. Verify evidence belongs to the candidate. "
                            "Profile visits can be visible to their owners. All recommendations need human review.",
            "page": {k: self.page[k] for k in ("url", "title", "text", "screenshot") if k in self.page}
            if self.page else None,
        })

    def _save(self):
        state = self.snapshot()
        if state["page"]:
            state["page"].pop("screenshot", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.part")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    def _log(self, action, **details):
        self.history.append({"action": action, "at": datetime.now(timezone.utc).isoformat(), **details})
        self._save()

    def _choose(self, page, actions, goal):
        if self.model_calls >= 300:
            raise ValueError("Reached the 300 request budget. Review saved profiles before starting again.")
        self.model_calls += 1
        self._log("Requested Jev browser decision")
        # The upstream operation and target heads see only supported reading actions.
        decision = choose({**page, "actions": actions}, goal, self.history)
        self.decision = {key: decision[key] for key in (
            "choice", "operation", "target", "model", "latency_ms", "confidence", "usage",
        )}
        self.decision.update({key: decision.get(key, {}) for key in (
            "operation_probabilities", "target_probabilities",
        )})
        self._log("Jev chose browser action", **self.decision)
        return decision["choice"]

    def _read_action(self, browser, page, action):
        browser.act(action, page)
        # Log before observing again. Failed mutations are never replayed.
        self._log("Executed Jev browser action", kind=action["kind"], action_id=action["id"])
        if action["kind"] == "wait":
            self.idle_steps += 1
            if self.idle_steps >= 3:
                raise ValueError("Jev waited three times without progressing. Inspect the connected browser.")
        else:
            self.idle_steps = 0

    def _observe(self, browser, expected=None):
        page = browser.observe(screenshot=True)
        self.page = page
        parsed = urlsplit(page["url"])
        text = page.get("text", "").lower()
        if (parsed.hostname != "www.linkedin.com" or
                any(part in parsed.path.lower() for part in ("login", "authwall", "checkpoint", "challenge")) or
                any(phrase in text for phrase in
                    ("sign in to linkedin", "verify your identity", "security verification"))):
            raise ValueError("LinkedIn requires login or verification. Complete it manually, then start a new run.")
        if expected and profile_url(page["url"]) != expected:
            raise ValueError("The profile redirected away from its observed URL. Review it manually.")
        if not expected and not parsed.path.startswith("/feed"):
            raise ValueError("LinkedIn did not open the feed. Open your timeline manually before starting a new run.")
        return page

    def command(self, name, body=None):
        body = body or {}
        if name == "review":
            if body.get("decision") not in {"shortlisted", "passed", "unreviewed"}:
                raise ValueError("Choose shortlisted, passed, or unreviewed.")
            candidate = next((c for c in self.candidates if c["profile_url"] == body.get("profile_url")), None)
            if candidate is None:
                raise ValueError("Candidate does not belong to this run.")
            candidate["review"] = body["decision"]
            self._log("Human review saved", profile_url=candidate["profile_url"], decision=body["decision"])
        elif name == "tick":
            if self.status in {"done", "blocked", "closed"}:
                return self.snapshot()
            self.status = "running"
            try:
                self._tick()
            except Exception as exc:
                self.status = "blocked"
                # Do not expose provider responses or credentials through persisted errors.
                self.error = (str(exc) if isinstance(exc, ValueError) else
                              "Recruiting stopped after a browser or provider error. Check Chrome remote debugging "
                              "and your Jev configuration, then start a new run.")
                self.message = self.error
                self._log("Run blocked", error=self.error)
            self._save()
        else:
            raise ValueError("Unknown recruiter command.")
        return self.snapshot()

    def _tick(self):
        if self.feed is None:
            self.feed = Browser(FEED_URL)
            self._log("Opened owned feed tab", url=FEED_URL)
            self._observe(self.feed)
            return
        if self.current:
            self._profile_tick()
            return
        if len(self.candidates) >= self.max_profiles:
            self.status, self.message = "done", "Profile budget reached. Review the collected evidence."
            return
        page = self._observe(self.feed)
        for action in page["actions"]:
            url = profile_url(action.get("href"))
            if not url or url in self.seen or len(self.seen) >= self.max_profiles:
                continue
            self.seen.add(url)
            self.discoveries.append({"profile_url": url, "discovered_from": page["url"],
                                     "source_context": action.get("label", "")[:1000]})
            self._log("Saved discovered profile link", profile_url=url)
            self.queue.append({"profile_url": url, "name": action.get("label", "LinkedIn profile")[:300],
                               "discovered_from": page["url"], "source_context": action.get("label", "")[:1000],
                               "evidence": [], "review": "unreviewed"})
        reviewed = {candidate["profile_url"] for candidate in self.candidates}
        actions = [a for a in page["actions"] if (
            a["kind"] == "click" and profile_url(a.get("href")) in self.seen
            and profile_url(a.get("href")) not in reviewed
        ) or (a["kind"] == "scroll" and a.get("delta", 0) > 0 and self.feed_scrolls < self.max_scrolls)
            or a["kind"] == "wait"]
        if not any(a["kind"] != "wait" for a in actions):
            self.status = "done"
            self.message = ("No unreviewed visible profiles or remaining feed scroll actions. "
                            "Saved links remain available.")
            return
        selected = self._choose(page, actions,
            "Discover and inspect people appearing in this LinkedIn feed for this role: " + self.requirements +
            " Open an unreviewed visible profile before scrolling for more. Profile links are saved automatically. "
            "Choose CLICK to inspect the selected observed profile in a separate tab. Scroll to discover more people. "
            "Only reading is supported. Never send messages or interact socially. "
            "Already reviewed profile URLs: " + json.dumps(sorted(reviewed)))
        if selected in {"DONE", "BLOCKED"}:
            self.status = "done" if selected == "DONE" else "blocked"
            self.message = "Jev stopped browsing. Review the saved links and evidence; coverage is not guaranteed."
            return
        action = next(a for a in actions if a["id"] == selected)
        if action["kind"] == "click":
            if not self.feed.fresh(page, action):
                raise ValueError("The chosen profile changed before navigation. Start again from the current feed.")
            url = profile_url(action["href"])
            self.current = next(c for c in self.queue if c["profile_url"] == url)
            self.queue.remove(self.current)
            # Consume only the selected observed link. Never let a model supply a URL.
            self.profile = Browser(url)
            self._log("Opened Jev selected profile in owned tab", profile_url=url)
            self.profile_scrolls = self.idle_steps = 0
        else:
            self._read_action(self.feed, page, action)
            if action["kind"] == "scroll":
                self.feed_scrolls += 1
        self.message = "Jev browser action completed."

    def _profile_tick(self):
        page = self._observe(self.profile, self.current["profile_url"])
        text = page.get("text", "")
        if text and text not in [e["text"] for e in self.current["evidence"]]:
            self.current["evidence"].append({"url": page["url"], "text": text})
        actions = [a for a in page["actions"] if a["kind"] == "wait" or
                   (a["kind"] == "scroll" and a.get("delta", 0) > 0)]
        if self.profile_scrolls < 2 and any(a["kind"] == "scroll" for a in actions):
            selected = self._choose(page, actions,
                "Read this person's professional profile for the following job requirements: " + self.requirements +
                " Scroll down to gather missing experience or qualifications. Choose DONE when the visible evidence "
                "is sufficient to assess, or no more useful reading is possible. Missing information stays unknown. "
                "Only scrolling or waiting is supported; never interact socially. Previously collected evidence: " +
                json.dumps([item["text"] for item in self.current["evidence"]]))
            if selected == "BLOCKED":
                raise ValueError("Jev could not progress through this profile. The discovered link is saved.")
            if selected != "DONE":
                action = next(a for a in actions if a["id"] == selected)
                self._read_action(self.profile, page, action)
                if action["kind"] == "scroll":
                    self.profile_scrolls += 1
                self.message = "Jev is gathering visible profile evidence."
                return
        evidence = [e["text"] for e in self.current["evidence"]]
        if self.model_calls >= 300:
            raise ValueError("Reached the 300 request budget. Discovered profile links remain saved.")
        self.model_calls += 1
        self._log("Requested Jev qualification and evidence choices", profile_url=self.current["profile_url"])
        output, metadata = assess(self.criteria, evidence, self.current["profile_url"])
        self.current["assessment"] = {**validate_assessment(output, self.criteria, evidence), **metadata}
        self.candidates.append(self.current)
        self._log("Profile evidence assessed by Jev", profile_url=self.current["profile_url"], **metadata)
        self.profile.close()
        self.profile, self.current = None, None
        self.message = "Jev assessment and profile link saved for human review."

    def close(self):
        for browser in (self.profile, self.feed):
            if browser:
                browser.close()
        self.profile, self.feed = None, None
        self.status = "closed"
        self.message = "Owned recruiting tabs closed. Saved candidate evidence remains available."
        self._save()
