"""Local-browser freshness/execution regressions. No model calls or external websites."""

import time
from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")

        browser.call("Page.navigate", url="data:text/html," + quote("""<!doctype html>
          <style>html,body{margin:0;height:100%;overflow:hidden}
          main{position:fixed;top:52px;bottom:0;width:100%;overflow-y:auto}
          section{height:500px}</style>
          <header>Persistent header</header><main><section><button>First profile</button></section>
          <section><button>Second profile</button></section>
          <section><button>Third profile</button></section><section>End</section></main>
        """))
        page = browser.observe(screenshot=False)
        down = next(a for a in page["actions"] if a["id"] == "scroll_down")
        first = next(a for a in page["actions"] if a["label"] == "First profile")
        assert browser.evaluate("document.documentElement.scrollHeight===innerHeight && scrollY===0")
        assert browser.evaluate(f"window.__jevFast.nodes.get({down['node']}).tagName") == "MAIN"
        assert not any(a.get("label") == "Third profile" for a in page["actions"])
        browser.act(down, page)
        # Wheel scrolling can complete asynchronously; wait only on observed state,
        # never resend a mutation. No model call is involved in this regression.
        deadline = time.monotonic() + 2
        while browser.evaluate("document.querySelector('main').scrollTop") < 500:
            assert time.monotonic() < deadline, "Observed container did not scroll"
            time.sleep(0.02)
        updated = browser.observe(screenshot=False)
        assert browser.evaluate("scrollY") == 0
        assert updated["scroll"]["y"] >= 500
        assert any(a.get("label") == "Third profile" for a in updated["actions"])
        assert not browser.fresh(page)
        assert not browser.fresh(page, first)
        passed.append("nested main scrolls through real wheel input and invalidates old guards")

        up = next(a for a in updated["actions"] if a["id"] == "scroll_up")
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999'; "
                         "document.body.append(cover)")
        try:
            browser.act(up, updated)
        except StalePage:
            pass
        else:
            raise AssertionError("Covered scroll surface accepted wheel input")
        passed.append("covered nested scroll surface rejected before input")

        browser.call("Page.navigate", url="data:text/html," + quote("""<!doctype html>
          <style>body{margin:0;height:2400px}</style><button>Window scroll</button>
        """))
        page = browser.observe(screenshot=False)
        down = next(a for a in page["actions"] if a["id"] == "scroll_down")
        browser.act(down, page)
        deadline = time.monotonic() + 2
        while browser.evaluate("scrollY") < 500:
            assert time.monotonic() < deadline, "Document did not scroll"
            time.sleep(0.02)
        assert browser.observe(screenshot=False)["scroll"]["y"] >= 500
        passed.append("ordinary document scrolling still uses real wheel input")
        browser.call("Page.navigate", url="data:text/html," + quote("""<!doctype html>
          <style>body{margin:20px}main{width:550px}aside{position:absolute;left:650px;top:20px;width:300px}
          .card{margin-bottom:15px}.photo{display:inline-block;width:24px;height:24px}
          .clip{height:22px;overflow:hidden}.below{padding-top:50px}</style>
          <main><article><div><a href="https://www.linkedin.com/in/founder/">Sam Founder</a>
          <p>Founder and software engineer</p></div>
          <p>We are hiring a field marketing manager in San Francisco.</p></article></main>
          <aside><div class="card"><a class="photo" href="https://www.linkedin.com/in/marketer/"></a>
          <div><a href="https://www.linkedin.com/in/marketer/?ref=sidebar">Alex Marketer</a>
          <p>Field Marketing Manager at Example</p><span hidden>Software engineer</span></div></div>
          <div class="card"><a href="https://www.linkedin.com/in/engineer/">Jo Engineer</a>
          <p>Software Engineer</p></div><div class="clip">
          <a href="https://www.linkedin.com/in/clipped/">Visible Name</a>
          <p class="below">Invisible field marketing title</p>
          <a href="https://www.linkedin.com/in/hidden/">Hidden profile</a></div></aside>
        """))
        page = browser.observe(screenshot=False)
        links = [a for a in page["actions"] if a.get("href")]
        marketer = [a for a in links if "/in/marketer/" in a["href"]]
        assert len(marketer) == 2
        assert all("Field Marketing Manager" in a["context"] for a in marketer)
        assert all("Engineer" not in a["context"] for a in marketer)
        assert all(a["region"] == "sidebar" for a in marketer)
        assert all(a["label"] == "Alex Marketer" for a in marketer)
        engineer = next(a for a in links if "/in/engineer/" in a["href"])
        assert "Marketing" not in engineer["context"]
        founder = next(a for a in links if "/in/founder/" in a["href"])
        assert "Founder and software engineer" in founder["context"]
        assert "hiring" not in founder["context"]
        assert founder["region"] == "main"
        clipped = next(a for a in links if "/in/clipped/" in a["href"])
        assert "Invisible" not in clipped["context"]
        assert "Invisible" not in page["text"]
        assert not any("/in/hidden/" in a["href"] for a in links)
        passed.append("profile card headlines stay with their person and exclude hidden and post text")
        browser.call("Page.navigate", url="data:text/html," + quote("""<!doctype html>
          <style>body{margin:0;height:2600px}main{width:550px}
          aside{position:absolute;left:650px;top:0;width:300px}
          .group{min-height:430px}.card{height:65px}</style>
          <main>Main profile</main><aside>
          <div class="group"><div><h3>More profiles for you</h3></div>
            <div class="card"><a href="https://www.linkedin.com/in/colleague/">Pat Colleague</a>
            <p>Field Marketing Manager</p></div></div>
          <section><h2>Advertisement</h2><a href="https://example.com">An ad</a></section>
          <div role="dialog" hidden><h2>Ad Options</h2>
            <a href="https://www.linkedin.com/in/hidden-ad/">Hidden ad profile</a></div>
          <div class="group"><div><h2>Get expert advice</h2></div>
            <div class="card"><a href="https://www.linkedin.com/in/expert/">Alex Expert</a>
            <p>Marketing Advisor</p></div></div>
          <section class="group"><header><h2>People you may know</h2></header>
            <div class="card"><a href="https://www.linkedin.com/in/peer/">Jo Peer</a>
            <p>Regional Field Marketing Manager</p></div></section>
          <section><h2>You might like</h2><a href="https://www.linkedin.com/company/example/">Example</a></section>
          <div><a href="https://www.linkedin.com/in/ungrouped/">Unclassified Person</a></div>
          </aside>
        """))
        page = browser.observe(screenshot=False)
        colleague = next(a for a in page["actions"] if "/in/colleague/" in a.get("href", ""))
        assert colleague["sidebar_section_index"] == 1
        assert colleague["sidebar_section_title"] == "More profiles for you"
        browser.evaluate("scrollTo(0,450)")
        page = browser.observe(screenshot=False)
        expert = next(a for a in page["actions"] if "/in/expert/" in a.get("href", ""))
        peer = next(a for a in page["actions"] if "/in/peer/" in a.get("href", ""))
        assert expert["sidebar_section_index"] == 2
        assert expert["sidebar_section_title"] == "Get expert advice"
        assert peer["sidebar_section_index"] == 3
        assert peer["sidebar_section_title"] == "People you may know"
        assert not any("/in/colleague/" in a.get("href", "") for a in page["actions"])
        assert "Pat Colleague" not in page["text"]
        browser.evaluate("scrollTo(0,900)")
        page = browser.observe(screenshot=False)
        peer = next(a for a in page["actions"] if "/in/peer/" in a.get("href", ""))
        ungrouped = next(a for a in page["actions"] if "/in/ungrouped/" in a.get("href", ""))
        assert peer["sidebar_section_index"] == 3
        assert "sidebar_section_index" not in ungrouped
        passed.append("sidebar section positions survive scrolling and exclude ads and unknown groups")
        browser.evaluate("scrollTo(0,0)")
        page = browser.observe(screenshot=False)
        expert = next(a for a in page["actions"] if "/in/expert/" in a.get("href", ""))
        browser.evaluate("const groups=document.querySelectorAll('.group'); "
                         "groups[0].append(groups[1].querySelector('.card'))")
        assert not browser.fresh(page, expert), "Moving a card into another section must invalidate"
        page = browser.observe(screenshot=False)
        expert = next(a for a in page["actions"] if "/in/expert/" in a.get("href", ""))
        assert expert["sidebar_section_index"] == 1
        browser.evaluate("const reorderedGroups=document.querySelectorAll('.group'); "
                         "reorderedGroups[0].before(reorderedGroups[2])")
        assert not browser.fresh(page, expert), "Reordering whole sections must invalidate"
        passed.append("profile action guards reject changed section membership and section order")
        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
