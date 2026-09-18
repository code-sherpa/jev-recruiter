"""Synthetic recruiting E2E with real Browser Harness and paid Jev calls.

Run with: uv run --env-file .env python scripts/check_recruiting_jev.py
Uses local fixture pages, never a LinkedIn account. Configure Browser Harness first.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import urlsplit

from jev_ultrafast import recruiter
from jev_ultrafast.browser import Browser

SEED = "https://www.linkedin.com/in/jev-synthetic-field-marketer/"
SIMILAR = "https://www.linkedin.com/in/jev-synthetic-event-marketer/"
ENGINEER = "https://www.linkedin.com/in/jev-synthetic-engineer/"
FOUNDER = "https://www.linkedin.com/in/jev-synthetic-founder/"
REQUIREMENTS = "Required: Owns B2B field marketing events\nRequired: Based in San Francisco"


def person_card(url, name, title):
    return f'<li><div><a href="{url}">{name}</a><p>{title}</p></div></li>'


class Fixture(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/search/":
            content = (
                '<main><h1>People results for field marketer</h1><ul>'
                + person_card(ENGINEER, "Alex Example", "Software Engineer at Example")
                + person_card(SEED, "Taylor Example", "B2B Field Marketing Manager at Example")
                + '</ul></main>'
            )
        elif self.path == "/seed/":
            content = (
                '<main><h1>Taylor Example</h1><h2>B2B Field Marketing Manager</h2>'
                '<p>Based in San Francisco.</p><p>I own B2B field marketing events.</p>'
                '<p>This is synthetic test data, not a real candidate.</p></main>'
                '<aside><h2>People also viewed</h2><ul>'
                + person_card(FOUNDER, "Jordan Example", "Founder and Software Engineer at Example")
                + person_card(SIMILAR, "Morgan Example", "B2B Field and Event Marketing Manager at Example")
                + '</ul></aside>'
            )
        elif self.path == "/similar/":
            content = (
                '<main><h1>Morgan Example</h1><h2>B2B Field and Event Marketing Manager</h2>'
                '<p>Based in San Francisco.</p><p>I own B2B field marketing events.</p>'
                '<p>This is synthetic test data, not a real candidate.</p></main>'
                '<aside><h2>People also viewed</h2><ul>'
                + person_card(FOUNDER, "Jordan Example", "Founder and Software Engineer at Example")
                + '</ul></aside>'
            )
        else:
            self.send_error(404)
            return
        body = ('<!doctype html><title>Synthetic recruiting fixture</title>'
                '<style>body{font:20px sans-serif;padding:40px;display:flex;gap:48px}'
                'main{width:800px}aside{width:380px}li{margin:20px 0;list-style:none}'
                'ul{padding:0}p{margin:12px 0}</style>' + content).encode()
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
    opened = []

    class FixtureBrowser(Browser):
        """Map synthetic URLs to local pages while preserving real DOM observations and guards."""

        def __init__(self, url, **viewport):
            parsed = urlsplit(url)
            if (parsed.scheme == "https" and parsed.netloc == "www.linkedin.com"
                    and parsed.path == "/search/results/people/"):
                path = "/search/"
            elif url in {SEED, SIMILAR}:
                path = "/seed/" if url == SEED else "/similar/"
            else:
                raise ValueError(f"The fixture refused an unrelated or unobserved URL: {url}")
            self.fixture_url = url
            opened.append(url)
            super().__init__(origin + path, **viewport)

        def observe(self, screenshot=True):
            state = super().observe(screenshot=screenshot)
            return {**state, "url": self.fixture_url}

    run = None
    try:
        with patch.object(recruiter, "Browser", FixtureBrowser):
            run = recruiter.Recruiter(REQUIREMENTS, max_profiles=2, max_scrolls=2)
            for _ in range(30):
                state = run.command("tick")
                if state["status"] in {"done", "blocked"}:
                    break
            assert state["status"] == "done", state.get("error") or state["message"]
            assert [url for url in opened if "/in/" in url] == [SEED, SIMILAR], opened
            assert [candidate["profile_url"] for candidate in state["candidates"]] == [SEED, SIMILAR]
            for candidate in state["candidates"]:
                assert candidate["assessment"]["recommendation"] == "potential_match", candidate["assessment"]
                assert all(c["quote"] for c in candidate["assessment"]["criteria"])
            similar = state["candidates"][1]
            assert similar["discovered_from"] == SEED, "Second visit must originate in the seed's sidebar"
            state = run.command("review", {"profile_url": SEED, "decision": "shortlisted"})
            persisted = json.loads(run.path.read_text())
            saved_urls = {item["profile_url"] for item in persisted["discoveries"]}
            assert {SEED, SIMILAR} <= saved_urls
            assert persisted["candidates"][0]["review"] == "shortlisted"
            assert ENGINEER not in opened and FOUNDER not in opened
            print(json.dumps({
                "verification": "Synthetic fixture, real Browser Harness and Jev, no LinkedIn account",
                "model": similar["assessment"]["model"], "model_calls": state["counts"]["model_calls"],
                "opened_profiles": [url for url in opened if "/in/" in url],
                "unrelated_profiles_opened": False, "sidebar_discovered_from": similar["discovered_from"],
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
