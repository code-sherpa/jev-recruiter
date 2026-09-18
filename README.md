# Jev Recruiter

A local LinkedIn sourcing workspace adapted from [Jev Ultrafast](https://github.com/browser-use/jev-ultrafast).

Edit the San Francisco engineering brief, open a session, and run discovery. The browser starts with a people search for forward deployed engineers or solutions engineers in San Francisco. Jev screens each visible professional title before opening a relevant profile, then follows relevant recommendations in that profile’s right sidebar. Unrelated or unknown titles cannot be opened. Profile evidence is compared with your requirements. Review the evidence yourself and mark candidates as shortlisted or passed.

## Run locally

```bash
uv sync
cp .env.example .env
# Configure TYPESAFE_API_KEY. Recruiting uses Jev only.
uv run browser-harness --doctor
uv run jev
```

Open **http://127.0.0.1:8766**. Connect Browser Harness to Chrome and sign into LinkedIn in that Chrome profile before starting. The app uses your existing browser session. It does not collect your LinkedIn password.

Jev is the only model used by the recruiting app. Navigation calls the original `model.choose` operation and target implementation. Title screening and qualification checks send status and indexed evidence choices together to the same Jev API. Evidence quotes are resolved from observed excerpts in code. No generative helper or secondary model is called. Model requests contain your job brief and observed profile text.

Browser control uses the upstream `browser-harness==0.1.13` dependency, including its CDP connection, observations, freshness checks, and scrolling. The recruiting viewport defaults to 2048 × 1280 so the right column remains visible; configure `RECRUITING_VIEWPORT_WIDTH` and `RECRUITING_VIEWPORT_HEIGHT` in `.env`. Observed scrolling supports both the document and visible scroll containers, including LinkedIn’s main feed panel. A selected observed profile link opens in an owned tab to preserve its source position. The original generic library remains in source for reference; its text helper is not exposed by the recruiting server. A clean upstream clone is available locally in `jev-ultrafast/` and is ignored by this repository.

## Workflow

1. Edit the suggested engineering requirements, one criterion per line (up to 20). Start optional criteria with `Preferred:`. Edit the starting search and set profile and discovery movement limits (scrolls and search page changes).
2. Start a session, then use the step or automatic run controls to collect profiles and evidence. Pause takes effect after the current request.
3. Review each criterion, its supporting quotation, and any missing information. The recommendation is an aid to your review, not a hiring decision.
4. Shortlist or pass candidates yourself. Export the results as JSON.

Only visible, observed LinkedIn profile URLs that pass Jev’s title relevance check are visited. Only the second and third headed profile sections in the sidebar supply recommendations. The first section and unidentified sections are excluded, even when their titles match. Section numbers persist while scrolling, and the source section is saved with each discovered link. Jev can select the observed Next button to continue through people search results. Eligible sidebar recommendations take priority over returning to search results. The recruiting flow performs navigation and scrolling. It does not send messages, connection requests, likes, or comments. Profile visits still follow your LinkedIn account visibility settings.

## Limits and data

The app samples a bounded number of visible profile screens (the interface requests up to seven). A supported match must have evidence for every required criterion. The target counter does not count unknown or conflicting assessments, and the review budget can stop a run before its target is reached. Collapsed experience, unavailable profiles, details below the sampled area, and content behind login are not evidence. Missing information stays unknown. Explicit mismatches require a supporting quotation. Location willingness and travel availability generally need direct confirmation. Assessment uses professional requirements, not protected personal traits.

All discovered URLs, including skipped titles and their screening status, plus the activity trail are saved locally under ignored `artifacts/recruiting/`. Export before starting another session. Reloading the page retains the current server session; restarting the server does not resume a previous run. The saved JSON files remain available on disk. The synthetic browser flow has been verified with real Browser Harness and Jev calls. The connected MacBook uses the signed in Chrome session.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/recruiter.js
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Unit tests use simulated browser and model responses and never call paid APIs.

For an explicit live integration check, run `uv run --env-file .env python scripts/check_recruiting_jev.py`. This uses real Browser Harness and paid Jev calls against local synthetic pages, never a LinkedIn account. It verifies profile choice, criterion evidence, immediate URL persistence, and saved shortlist review. Browser Harness must already be configured.

## Original project documentation

The documentation below describes the unmodified upstream generic agent and its original benchmarks. Its text helper and other model examples do not apply to this Jev only recruiting app.

<img src="docs/banner.svg" alt="Jev Ultrafast · Browser Use × TypeSafe" width="100%" />

# Jev Ultrafast ⚡

> [!IMPORTANT]
> **The Browser Use Cloud waitlist is open.** Get early access to ultrafast browser agents in the cloud.
> **[Join the waitlist →](https://browser-use.com/ultrafast?utm_source=github&utm_medium=readme&utm_campaign=jev-ultrafast)**

**A browser agent with a dynamic, indexed action space.**

Give it one goal. [TypeSafe's Jev](https://docs.typesafe.ai/introduction) picks an operation and an element. A small LLM writes text only when the operation is `TYPE_TEXT`.

**Zürich → London on Google Flights in 7.1 seconds.** One natural-language goal, actual text generation, and loading waits included.

<a href="docs/demo.mp4"><img src="docs/demo.gif" alt="A real Google Flights search at 1× speed, with generated city names and dynamic operation/target decisions" width="100%" /></a>

[Watch the MP4](docs/demo.mp4) · [Measurements](docs/performance.md) · [Read the loop](jev_ultrafast/agent.py)

## The action space

Every observation produces a new element table:

```text
[1] button    Change ticket type · Round trip
[2] combobox  Where from?        · San Francisco
[3] combobox  Where to?          · empty
[4] textbox   Departure          · empty
...
```

The operations are `CLICK`, `TYPE_TEXT`, `SELECT`, `SCROLL_UP`, `SCROLL_DOWN`, `WAIT`, `DONE`, and `BLOCKED`. Only supported operations and targets are offered.

```text
                      one TypeSafe request
                     ┌───────────────────────────┐
page → element table → operation                 │
                     │ click_target              │
                     │ type_text_target          │
                     │ select_target, if present │
                     └─────────────┬─────────────┘
                         use the matching target
                                   │
                    CLICK [7] ─────┤──→ browser
                TYPE_TEXT [3] ─────┘
                          ↓
                   small LLM → text → browser
```

Target questions are speculative. If the operation is `CLICK`, only `click_target` can execute. Two decisions, **one network round trip**. Each target head contains only compatible elements. Native dropdown choices carry an observed element/option index.

There are no site-specific action scripts or prepared field strings in the policy. The Flights example supplies a goal and independently verifies the outcome. The screenshot renderer adds labels afterward; it does not drive the browser.

## Try it

```bash
git clone https://github.com/browser-use/jev-ultrafast.git
cd jev-ultrafast
uv sync
cp .env.example .env
# Add TYPESAFE_API_KEY and TEXT_MODEL_API_KEY.
uv run jev
```

Open **http://127.0.0.1:8766** and click **Start demo → Run automatically**. The inspector shows numbered elements, operation probabilities, target probabilities, and executed actions. **Choose next** pauses before execution.

Chrome connects through [Browser Harness](https://github.com/browser-use/browser-harness), installed by `uv sync`. Run `uv run browser-harness --doctor` if it needs connecting. Allow remote debugging in Chrome when prompted.

`TEXT_MODEL_API_KEY` is an OpenRouter key in the example configuration. The current demo uses `inception/mercury-2.5` with reasoning disabled. Gemini, GLM, and DeepSeek can also use the OpenAI-compatible text helper; configure the appropriate model, endpoint, and reasoning setting.

## Use the library

```python
from jev_ultrafast import Agent

with Agent(
    "https://www.google.com/travel/flights?hl=en",
    "Find one-way flights from Zurich to London on September 20, 2026, "
    "for one adult in economy. Stop when matching flight options are visible.",
) as agent:
    for state in agent.run():
        print(state["elapsed_ms"], state["status"])
```

Run with `uv run --env-file .env python your_script.py`. The same policy can run a different task:

```bash
uv run --env-file .env python examples/run.py \
  --url https://en.wikipedia.org/wiki/Main_Page \
  --goal 'Find and open the Wikipedia article about Gödel’s incompleteness theorems.'
```

`uv run --env-file .env python examples/flights.py --keep-open` performs the flight search, checks the actual route/date/results, and saves its trace. It does not select or book a flight.

## Why it moves

- **One request per decision cycle.** Operation and target heads share the same observed state.
- **No screenshots in the default agent loop.** Jev consumes structured state. The inspector opts into screenshots; the video uses a separate continuous screencast.
- **One browser call per snapshot.** Read visible controls, their names, values, and text atomically. Keep references to the actual DOM nodes.
- **Validate the selected target.** Clicks check the document, form values, target, and nearby context. Animation alone does not force another prediction. Resolve current geometry and reject covered controls before input.
- **Wait for useful state.** After typing into a combobox, wait for visible suggestions, capped at 200 ms. Other interactions get at most two animation frames or 50 ms. These reads happen after execution is logged.
- **Keep hidden tabs rendering.** Focus emulation prevents background animation throttling without switching Chrome's visible tab.
- **Send visible text.** Offscreen article bodies and footers do not fill the model context.
- **Reuse an interrupted text request.** A generated value survives a stale-page retry only if the entire text-helper input is unchanged.

Every executed target is resolved from an observed node. The executor rechecks page freshness and click occlusion. Model output never becomes selectors, coordinates, shell commands, or executable JavaScript. Text-helper output must parse as a small JSON object before typing.

## Small enough to read

| File | Job |
| --- | --- |
| [agent.py](jev_ultrafast/agent.py) | The complete loop and text-helper handoff |
| [snapshot.js](jev_ultrafast/snapshot.js) | Atomic DOM snapshot, indexed controls, freshness guards |
| [browser.py](jev_ultrafast/browser.py) | Browser connection, current geometry, execution |
| [model.py](jev_ultrafast/model.py) | Dynamic operation/target heads and text generation |
| [questions.py](jev_ultrafast/questions.py) | Model instructions |
| [demo.py](jev_ultrafast/demo.py) | Local inspector |

## Evidence and limits

The current video is a **7,073 ms** Google Flights run. Timing starts after initial page observation and includes model calls, generated text, browser work, stale decisions, and loading waits. A fresh independent check verifies the one-way setting, Zürich, London, September 20, 2026, and visible flight options. The video plays at 1×, with no opening hold and a 0.5-second final hold.

In six alternating runs with identical models and settings, both versions passed **3/3**. Median task time went from **9.450 s → 7.092 s**, a **25% reduction**; median browser protocol calls went from **1,092 → 101**. This is three repeats of one task on one browser profile, not a general reliability benchmark.

The same policy opened the requested Wikipedia article in **2.798 s** and passed a local hotel search/filter task in **1.896 s**. Runs, failures, source hashes, and measurement boundaries are in [performance.md](docs/performance.md).

A `DONE` choice still requires independent outcome verification. The DOM reader handles common HTML and ARIA controls, not the full accessible-name specification. Shadow roots, frames, canvas, uploads, pop-up tabs, nested scrolling, and arbitrary keyboard widgets remain outside this MVP. Owned tabs share the existing Chrome profile.

## Development

```bash
uv run ruff check .
uv run pytest
node --check jev_ultrafast/static/app.js
node --check jev_ultrafast/snapshot.js
uv build
```

Tests are offline. `uv run python scripts/check_guards.py` checks real controls in a local browser without model calls. Live examples and recording scripts make paid API calls. `scripts/record_flights.py <new-folder>` captures original browser timestamps; `scripts/render_demo.py <recording-folder>` renders that verified run at 1× and crops out the Google account strip. Credentials and raw traces stay ignored.

---

[Browser Use](https://github.com/browser-use/browser-use) · [Browser Harness](https://github.com/browser-use/browser-harness) · [TypeSafe speculative fan-out](https://docs.typesafe.ai/patterns/fan-out)
