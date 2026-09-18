const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
const draft = `Role: Forward deployed engineer or solutions engineer (either title qualifies).
Experience: 3 to 5 years of relevant professional engineering experience, supported by visible employment dates or an explicit duration. Do not count overlapping roles twice. Missing dates remain unknown.
Location: Based in San Francisco or the San Francisco Bay Area.`;
let state = null, busy = false, automatic = false, filter = "all";
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const labels = {potential_match:"Potential match", needs_review:"Needs review", not_a_match:"Not a match"};
const reviewLabels = {unreviewed:"Awaiting your review", shortlisted:"On your shortlist", passed:"Passed"};
function safeProfileUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && (url.hostname === "linkedin.com" || url.hostname.endsWith(".linkedin.com")) ? url.href : null;
  } catch { return null; }
}
function allDiscoveries() {
  const candidates = state?.candidates || [];
  const assessed = new Set(candidates.map((candidate) => candidate.profile_url));
  return [...candidates, ...(state?.discoveries || []).filter((item) => !assessed.has(item.profile_url)).map((item) => ({...item, review:"unreviewed", pending:true}))];
}
function active() { return state && ["ready", "running"].includes(state.status); }
function controls() {
  $("start").disabled = busy || automatic;
  $("requirements").disabled = busy || automatic;
  $("search-query").disabled = busy || automatic;
  $("max-profiles").disabled = busy || automatic;
  $("max-scrolls").disabled = busy || automatic;
  $("target-matches").disabled = busy || automatic;
  $("step").disabled = busy || automatic || !active();
  $("run").disabled = busy || !active();
  $("run").hidden = automatic;
  $("pause").hidden = !automatic;
  $("pause").disabled = !automatic;
  $("close").disabled = busy || automatic || !state?.run_id || state.status === "closed";
  $("export").disabled = !allDiscoveries().length;
  document.querySelectorAll("[data-review]").forEach((button) => { button.disabled = busy || automatic; });
}
async function request(name, body) {
  const response = await fetch(`/api/recruiting/${name}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {"Content-Type":"application/json", "X-Demo-Token":token},
    ...(body === undefined ? {} : {body:JSON.stringify(body)}),
  });
  let data;
  try { data = await response.json(); }
  catch { throw new Error("The server returned an unreadable response. Check that the local app is running."); }
  if (!response.ok) throw new Error(data.error || "The request failed. Check the session before continuing.");
  state = data;
  render();
  return data;
}
async function perform(fn, message) {
  if (busy) return;
  busy = true;
  $("error").hidden = true;
  controls();
  $("status").textContent = message;
  try { await fn(); }
  catch (error) {
    automatic = false;
    try { await request("state"); } catch { /* Keep last observed state and the original failure. */ }
    $("error").textContent = error.message;
    $("error").hidden = false;
    $("status").textContent = "Paused. Needs attention.";
  } finally { busy = false; controls(); }
}
function renderCandidates() {
  const candidates = allDiscoveries();
  const visible = candidates.filter((candidate) => filter === "all" || candidate.review === filter || candidate.assessment?.recommendation === filter);
  $("candidate-count").textContent = candidates.length;
  if (!visible.length) {
    $("candidates").innerHTML = `<p class="candidates-empty">${candidates.length ? "no people in this view. try another filter." : "no profiles saved yet. start a search to find people."}</p>`;
    return;
  }
  $("candidates").innerHTML = visible.map((candidate) => {
    const assessment = candidate.assessment;
    const relevance = candidate.relevance?.status;
    const skipped = relevance === "unrelated" || relevance === "unknown";
    const screeningLabel = relevance === "unrelated" ? "Skipped: unrelated title" : relevance === "unknown" ? "Skipped: title unclear" : relevance === "relevant" ? "Relevant title" : "Awaiting title check";
    const recommendation = assessment?.recommendation;
    const url = safeProfileUrl(candidate.profile_url);
    const name = candidate.name || "Discovered profile";
    const initials = name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("");
    const criteria = assessment?.criteria || [];
    const review = candidate.review || "unreviewed";
    return `<article class="candidate-card"><div class="candidate-top"><div class="candidate-identity"><span class="avatar" aria-hidden="true">${escape(initials)}</span><div><h3 class="candidate-name">${url ? `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(name)} <span aria-hidden="true">↗</span></a>` : escape(name)}</h3><p class="candidate-source">${escape(candidate.discovered_from || "From discovery")}${candidate.sidebar_section_index ? ` · Sidebar section ${escape(candidate.sidebar_section_index)}: ${escape(candidate.sidebar_section_title || "Profile recommendations")}` : ""}</p></div></div><span class="badge ${recommendation === "potential_match" ? "match" : recommendation === "not_a_match" ? "unmatched" : ""}">${labels[recommendation] || screeningLabel}</span></div>
    <details class="listing-details"><summary>details &amp; evidence</summary>
    ${url ? `<a class="profile-link" href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(url)} ↗</a>` : ""}
    ${assessment?.model ? `<p class="assessment-model">Assessed by Jev · ${escape(assessment.model)}${Number.isFinite(assessment.latency_ms) ? ` · ${escape(assessment.latency_ms)} ms` : ""}</p>` : ""}
    <p class="candidate-summary">${escape(assessment?.summary || (skipped ? "Link saved without opening the profile. The visible title did not establish relevance." : "Profile link saved. Evidence will appear after the profile is visited."))}</p>
    ${candidate.relevance?.quote ? `<p class="candidate-summary">Title evidence: ${escape(candidate.relevance.quote)}</p>` : ""}
    <div class="criteria">${criteria.map((item) => `<div class="criterion"><span class="criterion-status ${item.status === "unknown" ? "unknown" : item.status === "not_met" ? "unmet" : ""}">${item.status === "met" ? "✓ Evidence found" : item.status === "not_met" ? "Does not meet" : "? Not confirmed"}</span><div><strong>${escape(item.criterion)}</strong>${item.quote ? `<blockquote>“${escape(item.quote)}”</blockquote>` : ""}</div></div>`).join("")}</div>
    ${(candidate.evidence || []).length ? `<details class="evidence"><summary>View observed evidence</summary>${candidate.evidence.map((item) => `<blockquote>${escape(item.text)}${safeProfileUrl(item.url) ? `<br /><a href="${escape(safeProfileUrl(item.url))}" target="_blank" rel="noopener noreferrer">View source ↗</a>` : ""}</blockquote>`).join("")}</details>` : ""}
    <div class="candidate-bottom"><span class="review-label">${escape(candidate.pending ? (skipped ? "Link saved. Profile skipped." : "Link saved. Assessment pending.") : reviewLabels[review] || reviewLabels.unreviewed)}</span><div class="review-actions">${url ? `<button type="button" data-copy="${escape(url)}" aria-label="Copy profile link for ${escape(name)}">Copy link</button>` : ""}${candidate.pending ? "" : `<button type="button" data-review="shortlisted" data-url="${escape(candidate.profile_url)}" aria-label="Shortlist ${escape(name)}" aria-pressed="${review === "shortlisted"}">Shortlist</button><button type="button" data-review="passed" data-url="${escape(candidate.profile_url)}" aria-label="Pass on ${escape(name)}" aria-pressed="${review === "passed"}">Pass</button>${review !== "unreviewed" ? `<button type="button" data-review="unreviewed" data-url="${escape(candidate.profile_url)}" aria-label="Clear review for ${escape(name)}">Undo</button>` : ""}`}</div></div></details></article>`;
  }).join("");
}
function renderDecision() {
  const decision = state?.decision;
  const calls = state?.counts?.model_calls || 0;
  $("model-calls").textContent = `${calls} model ${calls === 1 ? "call" : "calls"}`;
  $("decision-empty").hidden = !!decision;
  $("decision-metrics").hidden = !decision;
  $("decision-distribution").hidden = true;
  if (!decision) return;
  const confidence = Number.isFinite(decision.confidence) ? `${(decision.confidence * 100).toFixed(1)}%` : null;
  const metrics = [["Operation", decision.operation], ["Observed target", decision.target], ["Returned model", decision.model], ["Response time", Number.isFinite(decision.latency_ms) ? `${decision.latency_ms} ms` : null], ["Confidence", confidence]];
  $("decision-metrics").innerHTML = metrics.filter(([, value]) => value !== null && value !== undefined && value !== "").map(([label, value]) => `<div><dt>${escape(label)}</dt><dd>${escape(value)}</dd></div>`).join("");
  const operations = Object.entries(decision.operation_probabilities || {}).filter(([, probability]) => Number.isFinite(probability)).sort((a, b) => b[1] - a[1]);
  if (operations.length) {
    $("decision-distribution").hidden = false;
    $("decision-distribution").innerHTML = operations.map(([operation, probability]) => `<span class="decision-option ${operation === decision.operation ? "selected" : ""}">${escape(operation)} <b>${(probability * 100).toFixed(1)}%</b></span>`).join("");
  }
}
function render() {
  if (!state) return;
  const counts = state.counts || {};
  $("discovered-count").textContent = counts.discovered || 0;
  $("reviewed-count").textContent = counts.reviewed || 0;
  $("qualified-count").textContent = `${counts.qualified || 0} / ${state.target_matches || $("target-matches").value}`;
  $("shortlist-count").textContent = (state.candidates || []).filter((candidate) => candidate.review === "shortlisted").length;
  $("scroll-count").textContent = (counts.feed_scrolls || 0) + (counts.search_page_turns || 0);
  const statuses = {ready:"Ready for the next step",running:"Ready for the next step",blocked:"Paused. Needs attention.",done:"Discovery complete. Review your candidates.",closed:"Session ended. Your discoveries remain available."};
  $("status").textContent = automatic ? "Following relevant profiles and recommendations…" : state.message || statuses[state.status] || "Ready when you are";
  $("session-badge").textContent = automatic ? "RUNNING" : state.run_id ? ({running:"PAUSED", ready:"READY", blocked:"NEEDS ATTENTION", done:"COMPLETE", closed:"ENDED"}[state.status] || "READY") : "NOT STARTED";
  $("new-run-note").hidden = !allDiscoveries().length;
  if (state.error) { $("error").textContent = state.error; $("error").hidden = false; }
  const page = state.page;
  $("browser-url").textContent = page?.url || "Your LinkedIn search will appear here";
  $("page-title").textContent = page?.title || (state.run_id ? "Browser session" : "Waiting for your brief");
  $("screenshot").hidden = !page?.screenshot;
  $("browser-empty").hidden = !!page?.screenshot;
  if (page?.screenshot) $("screenshot").src = `data:image/jpeg;base64,${page.screenshot}`;
  if (page && !page.screenshot) $("browser-empty").innerHTML = '<span class="empty-icon" aria-hidden="true">↗</span><h3>Browser connected.</h3><p>No screenshot is available for this observation.<br />Follow the session in your connected Chrome.</p>';
  const history = state.history || [];
  $("activity-count").textContent = `${history.length} steps`;
  $("history").innerHTML = history.length ? [...history].reverse().map((entry) => `<li>${escape(String(entry.action || "Observed page").replaceAll("_", " "))}${entry.message ? ` · ${escape(entry.message)}` : ""}${entry.profile_url && safeProfileUrl(entry.profile_url) ? ` · <a href="${escape(safeProfileUrl(entry.profile_url))}" target="_blank" rel="noopener noreferrer">View profile ↗</a>` : ""}</li>`).join("") : "<li>No browser actions yet.</li>";
  renderCandidates();
  renderDecision();
  controls();
}
try { $("requirements").value = localStorage.getItem("jev.recruiting.engineering.requirements") ?? draft; }
catch { $("requirements").value = draft; }
$("requirements").addEventListener("input", () => { try { localStorage.setItem("jev.recruiting.engineering.requirements", $("requirements").value); } catch { /* The brief remains available for this session. */ } });
$("brief-form").addEventListener("submit", (event) => {
  event.preventDefault();
  automatic = false;
  const requirements = $("requirements").value.trim();
  const criteria = requirements.split(/\n/).filter((line) => line.trim());
  if (criteria.length > 20) { $("requirements").setCustomValidity("Use up to 20 criteria, one per line."); $("requirements").reportValidity(); return; }
  if (requirements.length < 10) { $("requirements").setCustomValidity("Describe the role in at least 10 characters."); $("requirements").reportValidity(); return; }
  perform(() => request("start", {requirements, search_query:$("search-query").value.trim(), max_profiles:Number($("max-profiles").value), max_scrolls:Number($("max-scrolls").value), target_matches:Number($("target-matches").value), max_profile_scrolls:6}), "Opening LinkedIn in your Chrome…");
});
$("requirements").addEventListener("input", () => $("requirements").setCustomValidity(""));
$("step").addEventListener("click", () => perform(() => { $("live-browser").open = true; return request("tick", {}); }, "Observing the next profile or search page…"));
$("run").addEventListener("click", () => perform(async () => {
  $("live-browser").open = true;
  automatic = true;
  controls();
  while (automatic && active()) {
    await request("tick", {});
    // Let controls and the page paint before scheduling another request.
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  automatic = false;
  render();
}, "Starting discovery…"));
$("pause").addEventListener("click", () => { automatic = false; $("status").textContent = "Pausing after the current browser step…"; $("pause").disabled = true; });
$("close").addEventListener("click", () => perform(() => request("close", {}), "Ending the browser session…"));
document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => {
  filter = button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  renderCandidates();
  controls();
}));
$("candidates").addEventListener("click", async (event) => {
  const copy = event.target.closest("[data-copy]");
  if (copy) {
    try { await navigator.clipboard.writeText(copy.dataset.copy); copy.textContent = "Copied"; }
    catch { $("error").textContent = "Clipboard access is unavailable. Open the profile link to copy its address."; $("error").hidden = false; }
    return;
  }
  const button = event.target.closest("[data-review]");
  if (button) perform(() => request("review", {profile_url:button.dataset.url, decision:button.dataset.review}), "Saving your review…");
});
$("export").addEventListener("click", () => {
  if (!state) return;
  const {page, ...rest} = state;
  const blob = new Blob([JSON.stringify({...rest, page:page ? {...page, screenshot:undefined} : null}, null, 2)], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "jev-recruiting-results.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
perform(async () => {
  await request("state");
  if (state.requirements) $("requirements").value = state.requirements;
  if (state.search_query) $("search-query").value = state.search_query;
  if (state.target_matches) $("target-matches").value = state.target_matches;
  if (state.max_profiles) $("max-profiles").value = state.max_profiles;
  if (state.max_scrolls) $("max-scrolls").value = state.max_scrolls;
}, "Connecting to your workspace…");

document.querySelectorAll('a[href="#role-settings"]').forEach(link => link.addEventListener("click", () => { $("role-settings").open = true; }));

$("brief-form").addEventListener("invalid", event => { const details = event.target.closest("details"); if (details) details.open = true; }, true);
