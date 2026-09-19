"""Bounded, read only LinkedIn sourcing with quoted evidence and human review."""

import copy
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from .browser import Browser, StalePage
from .decision_provider import decision_provider
from .discovery_model import screen_profiles
from .model import choose
from .recruiting_model import assess, parse_criteria

SEARCH_URL = "https://www.linkedin.com/search/results/people/"


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


def eligible_sidebar(action):
    """Only the second and third observed profile groups may supply recommendations."""
    index = action.get("sidebar_section_index")
    return action.get("region") == "sidebar" and type(index) is int and index in {2, 3}


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
    def __init__(self, requirements, max_profiles=10, max_scrolls=10,
                 search_query='"forward deployed engineer" OR "solutions engineer"', target_matches=None,
                 max_profile_scrolls=2):
        if not isinstance(requirements, str) or not 10 <= len(requirements.strip()) <= 12000:
            raise ValueError("Enter job requirements between 10 and 12000 characters.")
        for label, value in (("max_profiles", max_profiles), ("max_scrolls", max_scrolls)):
            limit = 50 if label == "max_profiles" else 500
            if type(value) is not int or not 1 <= value <= limit:
                raise ValueError(f"{label} must be an integer from 1 to {limit}.")
        if target_matches is not None and (type(target_matches) is not int or not 1 <= target_matches <= max_profiles):
            raise ValueError("target_matches must be an integer from 1 to max_profiles, or null.")
        if type(max_profile_scrolls) is not int or not 0 <= max_profile_scrolls <= 20:
            raise ValueError("max_profile_scrolls must be an integer from 0 to 20.")
        provider = decision_provider()
        self.provider, self.model = provider.name, provider.model
        self.provider_label = "Morph" if provider.name == "morph" else "Jev"
        self.viewport = {
            "width": int(os.environ.get("RECRUITING_VIEWPORT_WIDTH", "2048")),
            "height": int(os.environ.get("RECRUITING_VIEWPORT_HEIGHT", "1280")),
        }
        if not (800 <= self.viewport["width"] <= 3840 and 600 <= self.viewport["height"] <= 2160):
            raise ValueError("Recruiting viewport must be 800 to 3840 pixels wide and 600 to 2160 pixels tall.")
        if not isinstance(search_query, str) or not 2 <= len(search_query.strip()) <= 300:
            raise ValueError("Enter a starting search between 2 and 300 characters.")
        self.search_query = search_query.strip()
        self.search_url = SEARCH_URL + "?" + urlencode({"keywords": self.search_query})
        self.sources, self.visited, self.screen_cache = [], set(), {}
        self.run_id = uuid4().hex
        self.requirements = requirements.strip()
        self.max_profiles, self.max_scrolls = max_profiles, max_scrolls
        self.target_matches = target_matches
        self.max_profile_scrolls = max_profile_scrolls
        self.max_model_calls = 1000 if target_matches is not None else 300
        self.status, self.message, self.error = "ready", "Ready to find a relevant professional profile.", None
        self.criteria = parse_criteria(self.requirements)
        self.candidates, self.history = [], []
        self.queue, self.seen, self.discoveries = [], set(), []
        self.feed, self.profile, self.current = None, None, None
        self.page = None
        self.feed_scrolls = self.model_calls = 0
        self.search_page_turns = 0
        self.profile_scrolls = 0
        self.decision = None
        self.idle_steps = 0
        self.path = Path("artifacts/recruiting") / f"{self.run_id}.json"
        self._save()

    def snapshot(self):
        return copy.deepcopy({
            "search_query": self.search_query,
            "run_id": self.run_id, "requirements": self.requirements, "status": self.status,
            "message": self.message.replace("Jev", self.provider_label), "error": self.error, "criteria": self.criteria,
            "max_profiles": self.max_profiles, "max_scrolls": self.max_scrolls,
            "target_matches": self.target_matches,
            "max_profile_scrolls": self.max_profile_scrolls, "max_model_calls": self.max_model_calls,
            "target_reached": self.target_matches is not None and self.qualified_count >= self.target_matches,
            "counts": {"discovered": len(self.seen), "reviewed": len(self.candidates),
                       "qualified": self.qualified_count,
                       "queued": len(self.queue), "feed_scrolls": self.feed_scrolls,
                       "search_page_turns": self.search_page_turns,
                       "model_calls": self.model_calls},
            "candidates": self.candidates, "history": self.history,
            "discoveries": self.discoveries,
            "decision": self.decision, "model": self.model, "provider": self.provider,
            "provider_label": self.provider_label,
            "viewport": self.viewport,
            "limitations": f"At most {self.max_profile_scrolls + 1} visible profile screens are read. "
                            "Collapsed sections are not expanded. "
                            "Nearby recommendations may appear in excerpts. Verify evidence belongs to the candidate. "
                            "Profile visits can be visible to their owners. All recommendations need human review.",
            "page": {k: self.page[k] for k in ("url", "title", "text", "screenshot") if k in self.page}
            if self.page else None,
        })

    @property
    def qualified_count(self):
        """Count only evidence supported matches, independently of human shortlist choices."""
        return sum(candidate.get("assessment", {}).get("recommendation") == "potential_match"
                   for candidate in self.candidates)

    def _target_reached(self):
        if self.target_matches is None or self.qualified_count < self.target_matches:
            return False
        self.status = "done"
        self.message = (f"Found {self.qualified_count} profiles with evidence supporting all required criteria. "
                        "Human review is required.")
        return True

    def _save(self):
        state = self.snapshot()
        if state["page"]:
            state["page"].pop("screenshot", None)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.part")
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    def _log(self, action, **details):
        action = action.replace("Jev", self.provider_label)
        self.history.append({"action": action, "at": datetime.now(timezone.utc).isoformat(), **details})
        self._save()

    def _choose(self, page, actions, goal):
        if self.model_calls >= self.max_model_calls:
            raise ValueError(f"Reached the {self.max_model_calls} request budget. Review saved profiles.")
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
        if not expected and not parsed.path.startswith("/search/results/people/"):
            raise ValueError("LinkedIn did not open people search. Check the connected browser.")
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
            except StalePage:
                # Like the upstream Agent, discard an unexecuted stale choice.
                # The next tick observes again and asks Jev; no mutation is replayed.
                self.status = "ready"
                self.decision = None
                self.message = "Page changed before input. Ready for a fresh Jev decision."
                self._log("Discarded stale Jev decision before execution")
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

    def _screen(self, page, source):
        """Save observed links before screening their own visible professional evidence."""
        cards = {}
        for action in page["actions"]:
            url = profile_url(action.get("href"))
            if action["kind"] != "click" or not url or url in self.visited:
                continue
            if source["url"] and not eligible_sidebar(action):
                continue
            if action.get("region") == "navigation":
                continue
            card = {"profile_url": url, "label": action.get("label", ""),
                    "context": action.get("context", ""), "source_kind": "sidebar" if source["url"] else "search",
                    "sidebar_section_index": action.get("sidebar_section_index"),
                    "sidebar_section_title": action.get("sidebar_section_title", "")}
            if url not in cards or len(card["context"]) > len(cards[url]["context"]):
                cards[url] = card
            if url not in self.seen:
                self.seen.add(url)
                self.discoveries.append({"profile_url": url, "name": card["label"],
                                         "discovered_from": page["url"],
                                         "source_context": card["context"], "relevance": {"status": "pending"},
                                         "sidebar_section_index": card["sidebar_section_index"],
                                         "sidebar_section_title": card["sidebar_section_title"]})
                self._log("Saved discovered profile link", profile_url=url)
        pending = [c for c in cards.values() if (c["profile_url"], c["context"]) not in self.screen_cache]
        for start in range(0, len(pending), 30):
            if self.model_calls >= self.max_model_calls:
                raise ValueError(f"Reached the {self.max_model_calls} request budget. Discovered links remain saved.")
            batch = pending[start:start + 30]
            self.model_calls += 1
            self._log("Requested Jev title relevance screening", profiles=len(batch))
            results, metadata = screen_profiles(self.requirements, batch)
            for card in batch:
                self.screen_cache[(card["profile_url"], card["context"])] = results[card["profile_url"]]
            self._log("Jev screened observed professional titles", **metadata)
        eligible = set()
        for url, card in cards.items():
            result = self.screen_cache[(url, card["context"])]
            discovery = next(d for d in self.discoveries if d["profile_url"] == url)
            discovery.update(name=card["label"], source_context=card["context"], relevance=result)
            if result["status"] == "relevant" and result.get("quote") and result["quote"] in card["context"]:
                eligible.add(url)
        self.queue = [{"profile_url": url} for url in eligible]
        self._save()
        return eligible, cards

    def _tick(self):
        if self._target_reached():
            return
        if self.feed is None:
            self.feed = Browser(self.search_url, **self.viewport)
            self.sources.append({"browser": self.feed, "url": None, "rewind": 0})
            self._log("Opened owned people search tab", url=self.search_url)
            self._observe(self.feed)
            return
        if self.current:
            self._profile_tick()
            return
        if len(self.candidates) >= self.max_profiles:
            self.status, self.message = "done", "Profile budget reached. Review the collected evidence."
            if self.target_matches is not None:
                self.message += (f" Found {self.qualified_count} of {self.target_matches} requested matches. "
                                 "The match target was not reached.")
            return
        if not self.sources:
            self.status, self.message = "done", "No remaining relevant profile sources. Saved links remain available."
            return
        source = self.sources[-1]
        browser = source["browser"]
        page = self._observe(browser, source["url"])
        if "pending_navigation_since" in source:
            profiles = {profile_url(action.get("href")) for action in page["actions"]
                        if action["kind"] == "click" and action.get("region") == "main"
                        and profile_url(action.get("href"))}
            changed = (page["url"] != source["pending_navigation_url"]
                       or profiles != source["pending_navigation_profiles"])
            results_ready = bool(profiles) and changed
            explicitly_empty = any(phrase in page.get("text", "").casefold() for phrase in (
                "no results found", "no results for", "no matching results", "try broadening your search"))
            if not results_ready and not explicitly_empty:
                if time.monotonic() - source["pending_navigation_since"] >= 10:
                    raise ValueError("The next search page did not show results within 10 seconds. "
                                     "The navigation was not repeated; saved candidates remain available.")
                self.message = "Waiting for the selected search page to show results."
                return
            del source["pending_navigation_since"]
            del source["pending_navigation_url"]
            del source["pending_navigation_profiles"]
        if source["rewind"]:
            actions = [a for a in page["actions"] if a["kind"] == "scroll" and a.get("delta", 0) < 0]
            if actions:
                selected = self._choose(page, actions,
                    "Scroll up to return to this profile's similar people recommendations in the right sidebar.")
                if selected not in {"DONE", "BLOCKED"}:
                    action = next(a for a in actions if a["id"] == selected)
                    self._read_action(browser, page, action)
                    source["rewind"] -= 1
                    return
            source["rewind"] = 0
        eligible, cards = self._screen(page, source)
        # Image and name anchors often point to the same person. Offer one real
        # observed target per canonical URL, with its visible title beside its name.
        profile_actions = {}
        controls = []
        pagination_ids = set()
        discovery_budget_available = self.feed_scrolls + self.search_page_turns < self.max_scrolls

        def richness(action):
            return len(action.get("context", "")), len(action.get("label", ""))

        for action in page["actions"]:
            url = profile_url(action.get("href"))
            if (action["kind"] == "click" and url in eligible
                    and (not source["url"] or eligible_sidebar(action))):
                previous = profile_actions.get(url)
                if previous is None or richness(action) > richness(previous):
                    profile_actions[url] = action
            elif (not source["url"] and discovery_budget_available
                  and action["kind"] == "click" and action.get("role") == "button"
                  and action.get("label", "").strip().casefold() == "next"
                  and not action.get("href")):
                # LinkedIn people search exposes its next page as a native button.
                # It must still be visible, enabled, selected by Jev, and fresh at execution.
                controls.append(action)
                pagination_ids.add(action["id"])
            elif ((action["kind"] == "scroll" and action.get("delta", 0) > 0
                   and discovery_budget_available) or action["kind"] == "wait"):
                controls.append(action)
        actions = []
        for url, action in profile_actions.items():
            context = cards[url]["context"]
            label = action.get("label", "")
            if context and context not in label:
                label = label + "\n" + context
            actions.append({**action, "label": label})
        actions.extend(controls)
        if not any(a["kind"] != "wait" for a in actions):
            self.sources.pop()
            browser.close()
            self.message = "Finished this source. Returning to the previous relevant profile."
            return
        selected = self._choose(page, actions,
            "Choose a title screened profile to read, or continue navigating this source. "
            "Do not require a profile to meet every job requirement; qualification assessment happens after the visit. "
            "Offered profile links passed Jev's professional title screening for the starting search: " +
            self.search_query + ". Prefer titles most directly relevant to the requested role. "
            "Job requirements: " + self.requirements + ". "
            "Prioritize CLICK on a relevant recommendation from sidebar profile section two or three when offered, "
            "otherwise a relevant "
            "search result. The first sidebar profile section is excluded. "
            "If no eligible profile is visible, SCROLL_DOWN past the first section to reveal sections two and three. "
            "On search results, prefer relevant unvisited profiles; when none remain visible, choose the offered "
            "Next button to read the next results page, or scroll to reveal more results and pagination. "
            "Never open unrelated professional titles. Never send messages or interact socially.")
        if selected in {"DONE", "BLOCKED"}:
            self.sources.pop()
            browser.close()
            self.message = "Jev stopped this source; coverage is not guaranteed. Returning to remaining sources."
            if not self.sources:
                self.status = "done" if selected == "DONE" else "blocked"
            return
        action = next(a for a in actions if a["id"] == selected)
        if action["id"] in pagination_ids:
            self._read_action(browser, page, action)
            source["pending_navigation_since"] = time.monotonic()
            source["pending_navigation_url"] = page["url"]
            source["pending_navigation_profiles"] = {
                profile_url(a.get("href")) for a in page["actions"]
                if a["kind"] == "click" and a.get("region") == "main" and profile_url(a.get("href"))}
            self.search_page_turns += 1
            self.queue = []
        elif action["kind"] == "click":
            if not browser.fresh(page, action):
                raise StalePage("The chosen profile changed before navigation. Observe again.")
            url = profile_url(action["href"])
            card = cards[url]
            self.current = {"profile_url": url, "name": card["label"][:300],
                            "discovered_from": page["url"], "source_context": card["context"],
                            "sidebar_section_index": card["sidebar_section_index"],
                            "sidebar_section_title": card["sidebar_section_title"],
                            "relevance": self.screen_cache[(url, card["context"])],
                            "evidence": [], "review": "unreviewed"}
            self.profile = Browser(url, **self.viewport)
            self.visited.add(url)
            self.queue = [c for c in self.queue if c["profile_url"] != url]
            self._log("Opened Jev selected profile in owned tab", profile_url=url)
            self.profile_scrolls = self.idle_steps = 0
        else:
            self._read_action(browser, page, action)
            if action["kind"] == "scroll":
                self.feed_scrolls += 1
        self.message = "Jev browser action completed."

    def _profile_tick(self):
        page = self._observe(self.profile, self.current["profile_url"])
        self._screen(page, {"url": self.current["profile_url"]})
        text = page.get("profile_text", page.get("text", ""))
        if text and text not in [e["text"] for e in self.current["evidence"]]:
            self.current["evidence"].append({"url": page["url"], "text": text})
        actions = [a for a in page["actions"] if a["kind"] == "wait" or
                   (a["kind"] == "scroll" and a.get("delta", 0) > 0)]
        if self.profile_scrolls < self.max_profile_scrolls and any(a["kind"] == "scroll" for a in actions):
            selected = self._choose(page, actions,
                "Collect visible text about this person's professional experience, About section, and location. "
                "Read dated Experience entries to establish relevant professional experience duration when requested. "
                "Scroll past Activity when needed to reach Experience. Never infer age from professional dates. "
                "Scroll down to read experience that has not yet been observed. Choose DONE when these sections "
                "have been read or no further useful reading is possible. Do not assess job fit in this decision. "
                "Missing qualifications are not a reason to choose BLOCKED; assessment happens in a later step. "
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
        if self.model_calls >= self.max_model_calls:
            raise ValueError(f"Reached the {self.max_model_calls} request budget. Discovered links remain saved.")
        self.model_calls += 1
        self._log("Requested Jev qualification and evidence choices", profile_url=self.current["profile_url"])
        output, metadata = assess(self.criteria, evidence, self.current["profile_url"])
        self.current["assessment"] = {**validate_assessment(output, self.criteria, evidence), **metadata}
        self.candidates.append(self.current)
        self._log("Profile evidence assessed by Jev", profile_url=self.current["profile_url"], **metadata)
        self.sources.append({"browser": self.profile, "url": self.current["profile_url"],
                             "rewind": self.profile_scrolls})
        self.profile, self.current = None, None
        self.message = "Jev assessment and profile link saved for human review."
        self._target_reached()

    def close(self):
        for browser in {self.profile, self.feed, *(s["browser"] for s in self.sources)}:
            if browser:
                browser.close()
        self.sources = []
        self.profile, self.feed = None, None
        self.status = "closed"
        self.message = "Owned recruiting tabs closed. Saved candidate evidence remains available."
        self._save()
