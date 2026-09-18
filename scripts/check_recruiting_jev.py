"""Synthetic recruiting E2E with real Browser Harness and paid Jev calls.

Run with: uv run --env-file .env python scripts/check_recruiting_jev.py
Uses local fixture pages, never a LinkedIn account. Configure Browser Harness first.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from jev_ultrafast import recruiter
from jev_ultrafast.browser import Browser

PROFILE = "https://www.linkedin.com/in/jev-synthetic-fixture/"
REQUIREMENTS = "Required: Owns B2B field marketing events\nRequired: Based in San Francisco"


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        content = (
            '<h1>Synthetic LinkedIn feed</h1><article><h2>Field marketing event update</h2>'
            f'<a href="{PROFILE}">Taylor Example, B2B field marketer in San Francisco</a>'
            '<p>I own B2B field marketing events.</p></article>'
            if self.path == "/feed/" else
            '<h1>Taylor Example</h1><h2>B2B field marketer</h2>'
            '<p>Based in San Francisco.</p><p>I own B2B field marketing events.</p>'
            '<p>This is synthetic test data, not a real candidate.</p>'
        )
        body = ('<!doctype html><title>Synthetic recruiting fixture</title>'
                '<style>body{font:20px sans-serif;padding:40px;max-width:800px}</style>' + content).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_address[1]}"

    class FixtureBrowser(Browser):
        """Map only the synthetic test URLs to our local server; preserve real DOM guards."""

        def __init__(self, url, **viewport):
            if url not in {recruiter.FEED_URL, PROFILE}:
                raise ValueError("The fixture cannot visit other URLs")
            self.fixture_url = url
            super().__init__(origin + ("/feed/" if url == recruiter.FEED_URL else "/profile/"), **viewport)

        def observe(self, screenshot=True):
            state = super().observe(screenshot=screenshot)
            return {**state, "url": self.fixture_url}

    run = None
    try:
        with patch.object(recruiter, "Browser", FixtureBrowser):
            run = recruiter.Recruiter(REQUIREMENTS, max_profiles=1, max_scrolls=1)
            for _ in range(12):
                state = run.command("tick")
                if state["status"] in {"done", "blocked"}:
                    break
            assert state["status"] == "done", state.get("error")
            assert len(state["candidates"]) == 1, "Jev did not inspect the observed profile"
            candidate = state["candidates"][0]
            assert candidate["profile_url"] == PROFILE
            assert candidate["assessment"]["recommendation"] == "potential_match", candidate["assessment"]
            assert all(c["quote"] for c in candidate["assessment"]["criteria"])
            state = run.command("review", {"profile_url": PROFILE, "decision": "shortlisted"})
            persisted = json.loads(run.path.read_text())
            assert persisted["discoveries"][0]["profile_url"] == PROFILE
            assert persisted["candidates"][0]["review"] == "shortlisted"
            print(json.dumps({
                "verification": "Synthetic fixture, real Browser Harness and Jev, no LinkedIn account",
                "model": candidate["assessment"]["model"], "model_calls": state["counts"]["model_calls"],
                "browser_choice": state["decision"]["operation"],
                "recommendation": candidate["assessment"]["recommendation"],
                "saved_results": str(run.path), "passed": True,
            }, indent=2))
    finally:
        if run:
            run.close()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
