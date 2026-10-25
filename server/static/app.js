// Night Shift dashboard: connect repositories, watch the crew, review what needs a human.
const CREW = ["Watcher", "Triage Guard", "Fixer", "Night Manager"];
const STAGES = [
  ["spotted", "Spotted", "Watcher · Triage Guard"],
  ["proven", "Proven with a failing test", "Triage Guard"],
  ["fixed", "Fixed", "Fixer"],
  ["verified", "Checked by the autonomy gate", "Night Manager"],
  ["closed", "Closed", "Night Manager"],
];
const STATE_LABEL = { idle: "Idle", working: "Working", done: "Done", waiting: "Needs you" };
const NS = "http://www.w3.org/2000/svg";

let repos = [];
let current = null;   // "owner/name"
let data = null;      // {repo, board}
let selectedCase = null;
let view = null;      // "setup" | "connect" | "repo"

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const now = () => Date.now() / 1000;
const dur = (secs) => {
  if (secs == null || secs < 0) return "–";
  const s = Math.round(secs);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};
const ago = (ts) => {
  if (!ts) return "never";
  const s = now() - ts;
  return s < 60 ? "just now" : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
};

function toast(text) {
  const t = $("#toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}

async function api(path, options = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// ---- routing ----------------------------------------------------------------------------

async function route() {
  repos = await api("/api/repos").catch(() => []);
  const hash = location.hash.replace(/^#\/?/, "");
  if (hash.startsWith("repo/")) {
    current = hash.slice(5);
    if (!repos.find((r) => r.slug === current)) { location.hash = "#/"; return; }
    selectedCase = null;
    view = "repo";
    await loadRepo();
  } else if (hash === "setup" || (!repos.length && hash !== "connect" && !(await crewReady()))) {
    current = null;
    view = "setup";
    await renderSetup();
  } else if (hash === "connect" || !repos.length) {
    current = null;
    view = "connect";
    renderConnect();
  } else {
    location.hash = `#/repo/${repos[0].slug}`;
    return;
  }
  renderSwitch();
}

function renderSwitch() {
  $("#repo-switch").innerHTML = repos.map((r) => {
    const s = r.summary;
    const dot = s.breached ? "bad" : s.waiting ? "warn" : s.busy ? "busy" : s.mapped ? "ok" : "";
    return `<a href="#/repo/${esc(r.slug)}" class="side-link ${r.slug === current ? "active" : ""}"><span class="dot ${dot}"></span><span class="truncate">${esc(r.slug)}</span></a>`;
  }).join("") || `<p class="side-empty">None yet</p>`;
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", view === a.dataset.nav));
}

// ---- crew setup -------------------------------------------------------------------------

async function crewReady() {
  const s = await api("/api/setup").catch(() => null);
  return !s || Boolean(s.crew_webhook_url);
}

const ROUTINE = "Create a routine named \"Night Shift\" that runs when its webhook is called. When it runs: run " +
  "`~/.nightshift/venv/bin/nightshift wake '<the JSON body exactly as received>'`, post the result in the Night Shift " +
  "group chat, and hand off exactly as its HAND OFF line says. Give me the webhook URL and its Authorization header.";
const GROUP = "This is our working channel. Hand off with @Name, one owner per step: Watcher → Triage Guard → Fixer → Night Manager. Remember that.";
const RULES = "Allow automatically: running ~/.nightshift/venv/bin/nightshift commands, and file edits under ~/.nightshift/fixes/";

function copyBlock(text, label = "Copy") {
  const id = `copy-${copyBlock.n = (copyBlock.n || 0) + 1}`;
  copyBlock.texts[id] = text;
  return `<div class="copy-block"><pre>${esc(text)}</pre><button type="button" class="btn sm" data-copy-text="${id}">${esc(label)}</button></div>`;
}
copyBlock.texts = {};

async function renderSetup() {
  const s = await api("/api/setup");
  copyBlock.texts = {};
  const server = s.public_url || "<your dashboard's public URL>";
  const install = `curl -fsSL https://raw.githubusercontent.com/JacksonDescant/night-shift/main/scripts/install-crew.sh | ` +
    `NIGHTSHIFT_SERVER="${server}" NIGHTSHIFT_TOKEN="${s.ingest_token}" bash`;
  const step = (n, title, body) => `<li class="setup-step"><span class="mark">${String(n).padStart(2, "0")}</span><div><h2>${title}</h2>${body}</div></li>`;
  $("#view").innerHTML = `
    <section class="setup card">
      <div class="card-body">
        <div>
          <div class="eyebrow">One-time setup</div>
          <h1>Set up the crew</h1>
          <p class="muted">Night Shift's crew is four <a href="https://cursor.com/help/grok-bot/faqs" target="_blank" rel="noopener">Grok Bots</a>.
          Do this once, top to bottom; every value below is already filled in for this dashboard.</p>
          ${s.public_url ? "" : `<p class="error">No public URL: start the dashboard with <code>./setup.sh</code> so the crew can reach it.</p>`}
        </div>
        <ol class="setup-steps">
          ${step(1, "Create a GitHub token for the crew", `<p class="muted">At <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens</a>:
            only the repositories you'll connect; Contents and Pull requests, read and write. Keep it handy for step 4, and don't paste it into a chat.</p>`)}
          ${step(2, "Create the four Bots", `<p class="muted">For each: in Grok Bot, <b>+ → Create new Bot</b> with this name, then paste its instructions as the first message.</p>
            <div class="bot-prompts">${s.bots.map((b) => `<div class="bot-prompt"><b>${esc(b.name)}</b>
              <button type="button" class="btn sm" data-copy-text="${(copyBlock.texts[`bot-${b.key}`] = b.prompt, `bot-${b.key}`)}">Copy instructions</button></div>`).join("")}</div>`)}
          ${step(3, "Make their group chat", `<p class="muted">Create a group chat named <b>Night Shift</b> with all four Bots and send:</p>${copyBlock(GROUP)}`)}
          ${step(4, "Set up the Bots' computer", `<p class="muted">In any Bot's chat choose <b>Take over</b>, open a terminal, and run these two. The first asks for the token from step 1 (paste, Enter, Ctrl-D).</p>
            ${copyBlock("gh auth login --with-token")}${copyBlock(install)}<p class="muted">Every line should end <code>[ok]</code>.</p>`)}
          ${step(5, "Give the Watcher its routine", `<p class="muted">Send the Watcher:</p>${copyBlock(ROUTINE)}
            <form id="setup-form" class="stack">
              <label>Webhook URL it gives you<input name="crew_webhook_url" type="url" required autocomplete="off" value="${esc(s.crew_webhook_url)}"></label>
              <label>Its Authorization header<input name="crew_webhook_auth" type="password" autocomplete="off" placeholder="${s.crew_webhook_auth_set ? "Set. Leave blank to keep it" : ""}"></label>
              <div><button class="btn primary">Save</button></div>
            </form>`)}
          ${step(6, "Stop approvals from stalling it", `<p class="muted">In Grok Bot, <b>Settings → General → Auto-review → Custom Rules</b>, add:</p>${copyBlock(RULES)}`)}
        </ol>
        <div><a class="btn primary lg" href="#/connect">Connect a repository</a></div>
      </div>
    </section>`;
  $("#setup-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = e.target;
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ crew_webhook_url: form.crew_webhook_url.value, crew_webhook_auth: form.crew_webhook_auth.value }) });
      toast("Saved. Next, connect a repository");
    } catch (err) {
      toast(err.message);
    }
  });
}

// ---- connect ----------------------------------------------------------------------------

function renderConnect() {
  $("#view").innerHTML = `
    <section class="connect card">
      <div class="card-body">
        <div>
          <div class="eyebrow">New repository</div>
          <h1>Connect a repository</h1>
          <p class="muted">Night Shift's crew of four Grok Bots maps the repository, proves bugs with failing tests in an
          isolated environment, fixes them, and merges a fix on its own only when five safety checks pass. Everything else waits for you.</p>
        </div>
        <form id="connect-form" class="stack">
          <label>Repository
            <input name="repo" placeholder="owner/name or https://github.com/owner/name" required autocomplete="off" spellcheck="false">
          </label>
          <label>Fine-grained GitHub token
            <input name="token" type="password" required autocomplete="off" placeholder="github_pat_…">
            <small class="muted">Create one at <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens</a>
            for <b>only this repository</b>. Stored on this machine with owner-only permissions; never shown again.</small>
          </label>
          <ul class="perm-list">
            <li>Contents: read and write</li>
            <li>Pull requests: read and write</li>
            <li>Metadata: read (added automatically)</li>
          </ul>
          <div id="connect-error" class="error hidden" role="alert"></div>
          <div><button class="btn primary lg" id="connect-btn">Connect</button></div>
        </form>
        <div class="how">
          <div><span class="eyebrow">01</span><b>Map</b>The crew drafts a map of components, entry points and where data lives, and opens it as a PR for you.</div>
          <div><span class="eyebrow">02</span><b>Prove</b>Every suspected bug gets a test that fails in an isolated mimic before anything changes.</div>
          <div><span class="eyebrow">03</span><b>Fix</b>Small, local, provable fixes merge themselves. Anything touching auth or dependencies waits for you.</div>
        </div>
      </div>
    </section>`;
  $("#connect-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const form = new FormData(e.target);
    const btn = $("#connect-btn");
    btn.disabled = true;
    btn.textContent = "Checking with GitHub…";
    $("#connect-error").classList.add("hidden");
    try {
      const info = await api("/api/repos", { method: "POST", body: JSON.stringify({ repo: form.get("repo"), token: form.get("token") }) });
      if (info.note) toast(info.note);
      location.hash = `#/repo/${info.slug}`;
    } catch (err) {
      $("#connect-error").textContent = err.message;
      $("#connect-error").classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Connect";
    }
  });
}

// ---- repository view --------------------------------------------------------------------

async function loadRepo() {
  data = await api(`/api/repos/${current}`);
  renderRepo();
}

function waitingCases(b) {
  return Object.values(b.cases).filter((c) => c.escalated && !c.closed && !c.declined);
}

function liveCases(b) {
  return b.case_order.map((id) => b.cases[id]).filter((c) => c && !c.earlier);
}

function renderRepo() {
  const { repo, board: b } = data;
  const m = b.metrics;
  const status = b.map.status || {};
  const openCount = Object.keys(status.open_bugs || {}).length;
  const breached = (status.breached_assets || []).length;
  const busy = CREW.some((n) => b.crew[n].state === "working");
  const [owner, name] = repo.slug.split("/");
  const chips = [
    `<span class="chip mono">${esc(repo.default_branch)}</span>`,
    repo.private ? `<span class="chip">private</span>` : "",
    b.map.config ? `<span class="chip ok">mapped</span>` : `<span class="chip warn">no map yet</span>`,
    breached ? `<span class="chip bad">attack path to ${breached} asset${breached > 1 ? "s" : ""}</span>` : "",
    openCount ? `<span class="chip warn">${openCount} open bug${openCount > 1 ? "s" : ""}</span>` : `<span class="chip ok">no open bugs</span>`,
    busy ? `<span class="chip">crew at work</span>` : "",
  ].join("");

  $("#view").innerHTML = `
    <section class="repo-head">
      <div>
        <div class="eyebrow">Repository</div>
        <h1><a href="${esc(repo.url)}" target="_blank" rel="noopener"><span class="owner">${esc(owner)} /</span> ${esc(name)}</a></h1>
        ${repo.description ? `<p class="muted">${esc(repo.description)}</p>` : ""}
        <div class="chips">${chips}</div>
      </div>
      <div class="actions">
        <button class="btn" data-action="patrol" title="Check the riskiest components">Patrol</button>
        <button class="btn primary" data-action="sweep" title="Check every component">Full sweep</button>
        <details class="menu">
          <summary class="btn icon" aria-label="More actions"><svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3.5" cy="8" r="1.2"/><circle cx="8" cy="8" r="1.2"/><circle cx="12.5" cy="8" r="1.2"/></svg></summary>
          <div class="menu-list">
            <button data-action="map">Redraft the map</button>
            <button id="show-hook">Push webhook</button>
            <button id="disconnect" class="danger">Disconnect</button>
          </div>
        </details>
      </div>
    </section>
    <section class="stats">
      ${metric("Push to proof", dur(m.push_to_proof))}
      ${metric("Push to merge", dur(m.push_to_merge))}
      ${metric("Merged by the crew", m.auto_merges)}
      ${metric("Human reviews", m.human_touches)}
      ${metric("False alarms dismissed", m.dismissed)}
    </section>
    <div id="banners"></div>
    <section class="section"><div class="section-head"><h3>Crew</h3><span class="muted">${busy ? "working" : waitingCases(b).length ? "waiting on you" : "standing by"}</span></div>
      <ol class="pipeline" id="crew"></ol></section>
    <section class="panel map section"><div class="panel-head"><h3>Attack paths</h3><span class="muted" id="map-note"></span></div>
      <div id="map-body"></div></section>
    <section class="grid">
      <div class="panel"><div class="panel-head"><h3>Cases</h3><span class="muted">${liveCases(b).length || ""}</span></div>
        <div class="case-list" id="case-list" role="tablist"></div>
        <div class="panel-body" id="case-body"></div></div>
      <div class="panel"><div class="panel-head"><h3>Activity</h3></div>
        <div class="panel-body"><ol class="activity" id="activity"></ol></div></div>
    </section>`;

  document.querySelectorAll("[data-action]").forEach((btn) => btn.addEventListener("click", () => { btn.closest("details")?.removeAttribute("open"); action(btn.dataset.action); }));
  $("#show-hook").addEventListener("click", showHook);
  $("#disconnect").addEventListener("click", disconnect);
  renderBanners(b);
  renderCrew(b);
  renderCases(b);
  renderMap(b);
  renderActivity(b);
}

function metric(label, value) {
  return `<div class="stat"><small>${esc(label)}</small><b>${esc(value)}</b></div>`;
}

function renderBanners(b) {
  const out = [];
  if (!b.map.config) {
    out.push(`<div class="banner info"><b class="title">Mapping the repository</b>
      The crew is drafting a map of its components, entry points and where data lives. It will open the map as a pull request for you to review.</div>`);
  } else if (b.map.pr && !b.map.status) {
    out.push(`<div class="banner info"><b class="title">Review the crew's map</b>
      ${esc(b.map.summary)} Merge the PR to start patrols: ${esc(b.map.pr)}</div>`);
  }
  const waiting = waitingCases(b);
  if (waiting.length) {
    out.push(`<div class="banner warn"><b class="title">Waiting for you: ${waiting.length} fix${waiting.length > 1 ? "es" : ""} the crew won't merge alone</b>
      <ul>${waiting.map((c) => {
        const url = b.prs[c.bug];
        return `<li><b>${esc(c.title)}</b><small>${esc((c.escalated || []).join("; "))}</small>
          ${url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">Review on GitHub</a>` : ""}</li>`;
      }).join("")}</ul>
      <small class="muted">Merge or close it on GitHub. The dashboard notices within ~20 seconds and the crew carries on.</small></div>`);
  }
  const v = b.verdict;
  if (v && v.clean && b.cycle.end) {
    const what = v.message ? `“${esc(v.message)}”` : "The latest change";
    out.push(`<div class="banner ok"><b class="title">All clear</b>${what}: the crew checked ${esc(namesOf(b, v.components))} and found no bugs.
      ${v.dismissed ? `${v.dismissed} scanner finding${v.dismissed > 1 ? "s" : ""} reviewed and dismissed.` : ""}</div>`);
  }
  $("#banners").innerHTML = out.join("");
}

function namesOf(b, ids) {
  const comps = (b.map.config || {}).components || {};
  if (!ids || !ids.length) return "the change";
  return ids.map((id) => (comps[id] || {}).name || id).join(", ");
}

function renderCrew(b) {
  $("#crew").innerHTML = CREW.map((name, i) => {
    const bot = b.crew[name];
    return `<li class="bot ${bot.state}">
      <div class="bot-top"><span class="bot-n">0${i + 1}</span><span class="state ${bot.state}">${esc(STATE_LABEL[bot.state] || bot.state)}</span><time id="timer-${i}"></time></div>
      <b>${esc(name)}</b>
      <small>${esc(bot.role)}</small>
      <div class="bot-step">${esc(bot.step || (bot.state === "idle" ? "Waiting for a push or a patrol" : ""))}</div>
    </li>`;
  }).join("");
  tick();
}

function tick() {
  if (!data) return;
  CREW.forEach((name, i) => {
    const el = $(`#timer-${i}`);
    if (!el) return;
    const bot = data.board.crew[name];
    const end = bot.state === "working" ? now() : bot.finished;
    el.textContent = bot.started && end ? dur(end - bot.started) : "";
  });
}

// ---- cases ------------------------------------------------------------------------------

function caseStatus(c) {
  if (c.closed) return c.closed.how === "auto" ? ["ok", "merged by the crew"] : ["ok", "merged after human review"];
  if (c.declined) return ["warn", "declined by a human"];
  if (c.escalated) return ["warn", "waiting for a human"];
  if (c.retrying) return ["", "retrying the merge"];
  if (data.board.prs[c.bug]) return ["", "fix waiting to merge"];
  return CREW.some((n) => data.board.crew[n].state === "working") ? ["", "crew working"] : ["warn", "open"];
}

function renderCases(b) {
  const cases = liveCases(b);
  if (!cases.find((c) => c.bug === selectedCase)) selectedCase = cases[0]?.bug || null;
  $("#case-list").innerHTML = cases.map((c) => {
    const [kind] = caseStatus(c);
    const [, label] = caseStatus(c);
    return `<button class="case-row" role="tab" aria-selected="${c.bug === selectedCase}" data-case="${esc(c.bug)}">
      <span class="dot ${kind === "ok" ? "ok" : kind === "warn" ? "warn" : "busy"}"></span><span class="truncate">${esc(c.title)}</span>
      <small>${esc(label)}</small></button>`;
  }).join("");
  document.querySelectorAll("[data-case]").forEach((el) => el.addEventListener("click", () => { selectedCase = el.dataset.case; renderCases(data.board); }));

  const c = b.cases[selectedCase];
  if (!c) {
    $("#case-body").innerHTML = `<div class="empty"><b>No cases yet</b>When a push or a patrol turns up a real bug, its case appears here with the evidence: the proof, the fix and the checks.</div>`;
    return;
  }
  const [kind, label] = caseStatus(c);
  const comp = namesOf(b, c.component ? [c.component] : []);
  const current = STAGES.findIndex(([key]) => !c[key]);
  const open = (b.map.status?.open_bugs || {})[c.bug] && !b.prs[c.bug] && !c.closed;
  $("#case-body").innerHTML = `
    <div class="case-title"><h2>${esc(c.title)}</h2><span class="chip ${kind}"><span class="dot ${kind || "busy"}"></span>${esc(label)}</span></div>
    <p class="muted mono-sm">${esc(c.bug)}${c.component ? ` · ${esc(comp)}` : ""}</p>
    ${open ? `<button class="btn" id="fix-this">Ask the crew to fix this</button>` : ""}
    <ol class="stages">${STAGES.map(([key, title, who], i) => stageRow(c, key, title, who, i, current)).join("")}</ol>`;
  $("#fix-this")?.addEventListener("click", () => action("fix", { bug: c.bug }));
}

function stageRow(c, key, title, who, i, current) {
  const info = c[key];
  const blocked = key === "closed" && !info && (c.escalated || c.declined);
  const cls = info ? "done" : blocked ? "blocked" : i === current && !c.closed ? "current" : "pending";
  const start = data.board.cycle?.start;
  const t = info && start && info.ts >= start ? `+${dur(info.ts - start)}` : "";
  return `<li class="stage ${cls}">
    <span class="mark">${info ? "✓" : blocked ? "!" : i + 1}</span>
    <div class="stage-head"><b>${title}</b><span class="who">${who}</span><time>${t}</time></div>
    <div class="stage-body">${stageBody(c, key, info)}</div>
  </li>`;
}

function diffHtml(diff) {
  return diff.split("\n").filter((l) => !l.startsWith("diff --git") && !l.startsWith("index ")).map((l) => {
    const cls = l.startsWith("@@") ? "hunk" : l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : "";
    return `<span class="${cls}">${esc(l)}</span>`;
  }).join("\n");
}

function stageBody(c, key, info) {
  if (key === "closed" && !info && c.declined) return `<p>A human closed the PR without merging. The bug stays open and the crew won't retry it in this task.</p>`;
  if (key === "closed" && !info && c.escalated) {
    const url = data.board.prs[c.bug];
    return `<p>The crew stopped on purpose: ${esc(c.escalated.join("; "))}.</p>
      ${url ? `<a class="btn" href="${esc(url)}" target="_blank" rel="noopener">Review the PR on GitHub</a>` : ""}`;
  }
  if (key === "closed" && !info && c.retrying) return `<p>All checks passed. GitHub hasn't merged the PR yet (${esc(c.retrying.join("; "))}); the Night Manager is retrying.</p>`;
  if (!info) return "";
  if (key === "spotted") return `<p>${esc(info.source)}</p>${info.reason ? `<p class="muted">${esc(info.reason)}</p>` : ""}`;
  if (key === "proven") {
    return `<p>The proof <code>${esc(info.test_path || "")}</code> fails on the current code in the mimic, so the bug is real.</p>
      ${info.test_code ? `<pre>${esc(info.test_code.trim())}</pre>` : ""}
      ${info.failure ? `<pre class="fail">${esc(info.failure)}</pre>` : ""}`;
  }
  if (key === "fixed") {
    const pr = info.url ? `<a href="${esc(info.url)}" target="_blank" rel="noopener">a pull request</a>` : "a fix branch";
    return `<p>Opened ${pr}${info.lines ? ` changing ${info.lines} line${info.lines > 1 ? "s" : ""}` : ""}.${info.summary ? ` ${esc(info.summary)}` : ""}</p>
      ${info.diff ? `<pre class="diff">${diffHtml(info.diff)}</pre>` : ""}`;
  }
  if (key === "verified") {
    const checks = (info.checks || []).map((x) => `<li class="${x.ok ? "ok" : "no"}"><span>${x.ok ? "✓" : "✗"}</span><span>${esc(x.name)}</span><small>${esc(x.detail)}</small></li>`).join("");
    return `<ul class="checks">${checks}</ul><p class="muted">${info.verdict === "auto_merge" ? "All five passed: the crew may merge this on its own." : "Not all passed: this needs a human."}</p>`;
  }
  if (key === "closed") return info.how === "auto" ? `<p>Merged by the crew. No human needed.</p>` : `<p>A human reviewed and merged the fix.</p>`;
  return "";
}

// ---- attack-path map --------------------------------------------------------------------

function el(tag, attrs, parent) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function layout(config) {
  const comps = config.components || {};
  const ids = Object.keys(comps);
  if (!ids.length) return { pos: {}, out: {}, W: 168, H: 58, width: 0, height: 0 };
  const out = Object.fromEntries(ids.map((id) => [id, []]));
  for (const [a, b] of config.links || []) if (out[a] && comps[b]) out[a].push(b);
  const depth = {};
  const queue = ids.filter((id) => comps[id].entry);
  queue.forEach((id) => (depth[id] = 0));
  while (queue.length) {
    const id = queue.shift();
    for (const next of out[id]) if (depth[next] === undefined) { depth[next] = depth[id] + 1; queue.push(next); }
  }
  const maxDepth = Math.max(0, ...Object.values(depth));
  ids.forEach((id) => { if (depth[id] === undefined) depth[id] = comps[id].asset ? maxDepth + 1 : maxDepth + 1; });
  const columns = {};
  ids.forEach((id) => (columns[depth[id]] = columns[depth[id]] || []).push(id));
  const W = 168, H = 58, GX = 52, GY = 18;
  const pos = {};
  const cols = Object.keys(columns).map(Number).sort((a, b) => a - b);
  const tallest = Math.max(...cols.map((c) => columns[c].length));
  cols.forEach((c, ci) => {
    const list = columns[c].sort();
    const offset = ((tallest - list.length) * (H + GY)) / 2;
    list.forEach((id, ri) => (pos[id] = { x: 12 + ci * (W + GX), y: 12 + offset + ri * (H + GY) }));
  });
  return { pos, out, W, H, width: 24 + cols.length * (W + GX) - GX, height: 24 + tallest * (H + GY) - GY };
}

function withProposals(config, proposals) {
  // The committed map plus what the crew has learned since, marked so it draws dashed.
  const out = { components: { ...config.components }, links: (config.links || []).map((l) => [...l]), proposed: new Set() };
  for (const p of proposals) {
    if (p.kind === "component" && !out.components[p.id]) {
      out.components[p.id] = { name: p.name, paths: p.paths, entry: p.entry, asset: p.asset };
      out.proposed.add(p.id);
    }
  }
  for (const p of proposals) {
    if (p.kind === "link" && out.components[p.from] && out.components[p.to]) {
      out.links.push([p.from, p.to]);
      out.proposed.add(`${p.from}>${p.to}`);
    }
  }
  return out;
}

const KIND_TEXT = {
  link: (p) => `${p.from} → ${p.to}`,
  component: (p) => `new component ${p.id}`,
  stale: (p) => `${p.path} is gone from ${p.component}`,
  entry: (p) => `${p.component} is an entry point`,
  asset: (p) => `${p.component} holds data`,
};

let selectedNode = null;

function renderMap(b) {
  const config = b.map.config;
  const body = $("#map-body");
  if (!config || !Object.keys(config.components || {}).length) {
    body.innerHTML = `<div class="empty panel-body"><b>No map yet</b>Once the crew maps the repository, its components and any attack paths through them show up here.</div>`;
    $("#map-note").textContent = "";
    return;
  }
  const status = b.map.status || {};
  const proposals = b.map.proposals || [];
  const stats = b.map.stats || {};
  const view = withProposals(config, proposals);
  const open = new Set(status.open_components || []);
  const reached = new Set(status.reached || []);
  const breachEdges = new Set((status.breach_paths || []).flatMap((p) => p.slice(1).map((n, i) => `${p[i]}>${n}`)));
  const fixComp = (status.open_bugs || {})[b.map.recommended_fix];
  const scanning = new Set(b.scanning ? b.patrol.components || [] : []);
  const openCount = {};
  for (const comp of Object.values(status.open_bugs || {})) openCount[comp] = (openCount[comp] || 0) + 1;
  const { pos, out, W, H, width, height } = layout(view);
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": "Component map with attack paths" });
  const edges = el("g", {}, svg);
  for (const [a, targets] of Object.entries(out)) {
    for (const t of targets) {
      const p = pos[a], q = pos[t];
      if (!p || !q) continue;
      const x1 = p.x + W, y1 = p.y + H / 2, x2 = q.x, y2 = q.y + H / 2;
      const mx = (x1 + x2) / 2;
      const cls = breachEdges.has(`${a}>${t}`) ? "breach" : view.proposed.has(`${a}>${t}`) ? "proposed" : "";
      el("path", { class: `edge ${cls}`, d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}` }, edges);
    }
  }
  for (const [id, comp] of Object.entries(view.components)) {
    const p = pos[id];
    const cls = ["node", comp.entry && "entry", comp.asset && "asset", reached.has(id) && !comp.entry && "reached",
      open.has(id) && "open", id === fixComp && "next", scanning.has(id) && "scanning",
      view.proposed.has(id) && "proposed", id === selectedNode && "selected"].filter(Boolean).join(" ");
    const g = el("g", { class: cls, transform: `translate(${p.x},${p.y})`, tabindex: 0, role: "button", "data-node": id }, svg);
    el("rect", { width: W, height: H, rx: 6 }, g);
    const name = el("text", { x: 10, y: 22 }, g);
    name.textContent = (comp.name || id).slice(0, openCount[id] ? 17 : 22);
    if (openCount[id]) {
      const n = el("text", { x: W - 10, y: 22, class: "count", "text-anchor": "end" }, g);
      n.textContent = `${openCount[id]} open`;
    }
    const sub = el("text", { x: 10, y: 40, class: "sub" }, g);
    const st = stats[id];
    sub.textContent = view.proposed.has(id) ? "proposed" :
      [comp.entry && "entry", comp.asset && "data", st?.fixed && `${st.fixed} fixed`].filter(Boolean).join(" · ") || id;
  }
  body.innerHTML = `<div class="map-layout"><div class="map-canvas"></div><div class="map-side"></div></div>`;
  const canvas = $(".map-canvas", body), side = $(".map-side", body);
  canvas.appendChild(svg);
  const breached = (status.breached_assets || []).length;
  const learned = proposals.length ? `
    <div class="learned">
      <div class="learned-head"><b>Learned since the map was merged</b><span class="muted">${proposals.length} change${proposals.length > 1 ? "s" : ""}</span></div>
      <ul>${proposals.slice(0, 6).map((p) => `<li><span class="tag ${p.source}">${p.source === "agent" ? "crew" : "code"}</span>
        <span>${esc((KIND_TEXT[p.kind] || (() => p.kind))(p))}</span><small>${esc(p.why || "")}</small></li>`).join("")}
        ${proposals.length > 6 ? `<li class="muted">and ${proposals.length - 6} more</li>` : ""}</ul>
      ${b.map.update_pr ? `<a class="btn sm" href="${esc(prUrl(b.map.update_pr))}" target="_blank" rel="noopener">Review the map update</a>`
        : `<button class="btn sm" id="learn-map">Propose a map update</button>`}
    </div>` : "";
  canvas.insertAdjacentHTML("beforeend", `
    <p class="why">${breached ? `<b>An attacker can reach ${esc(namesOf(b, status.breached_assets))}.</b> ` : ""}${esc(b.map.reason || "")}</p>
    <div class="legend"><span><i class="l-entry"></i>entry point</span><span><i class="l-asset"></i>holds data</span>
    <span><i class="l-open"></i>open bug</span><span><i class="l-reached"></i>reachable</span><span><i class="l-next"></i>fix next</span>
    ${proposals.length ? `<span><i class="l-proposed"></i>learned</span>` : ""}</div>`);
  side.innerHTML = `<div id="node-detail"></div>${learned}<div id="exposure"></div>
    ${!proposals.length && (b.map.history || []).length < 2 && !selectedNode ? `<p class="muted side-hint">Click a component for what the crew has found in it.</p>` : ""}`;
  $("#learn-map")?.addEventListener("click", () => action("learn"));
  svg.querySelectorAll("[data-node]").forEach((node) => {
    const pick = () => { selectedNode = selectedNode === node.dataset.node ? null : node.dataset.node; renderMap(data.board); };
    node.addEventListener("click", pick);
    node.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
  renderNodeDetail(b, view);
  renderExposure(b);
  $("#map-note").textContent = b.scanning ? `checking ${namesOf(b, b.patrol.components)}` : "";
}

function prUrl(text) {
  return (String(text).match(/https?:\/\/\S+/) || ["#"])[0];
}

function renderNodeDetail(b, view) {
  const box = $("#node-detail");
  const comp = view.components[selectedNode];
  if (!comp) { box.innerHTML = ""; selectedNode = null; return; }
  const st = (b.map.stats || {})[selectedNode] || {};
  const openBugs = Object.entries(b.map.status?.open_bugs || {}).filter(([, c]) => c === selectedNode).map(([bug]) => bug);
  const caseLink = (bug) => `<button class="link" data-goto-case="${esc(bug)}">${esc(b.cases[bug]?.title || bug)}</button>`;
  const notes = (b.map.proposals || []).filter((p) => [p.id, p.from, p.to, p.component].includes(selectedNode));
  box.innerHTML = `
    <div class="node-detail">
      <div class="nd-head"><div><span class="eyebrow">${view.proposed.has(selectedNode) ? "Proposed component" : "Component"}</span>
        <b>${esc(comp.name || selectedNode)}</b></div><button class="btn icon sm" id="nd-close" aria-label="Close">×</button></div>
      <div class="nd-stats">
        <div><small>Findings</small><b>${st.findings || 0}</b></div><div><small>Dismissed</small><b>${st.dismissed || 0}</b></div>
        <div><small>Proven</small><b>${st.proven || 0}</b></div><div><small>Fixed</small><b>${st.fixed || 0}</b></div>
      </div>
      <dl class="kv">
        <dt>Paths</dt><dd>${(comp.paths || []).map(esc).join("<br>") || "–"}</dd>
        <dt>Role</dt><dd>${[comp.entry && "entry point", comp.asset && "holds data"].filter(Boolean).join(", ") || "internal"}</dd>
        <dt>Last checked</dt><dd>${esc(ago(st.last_checked))}</dd>
        ${openBugs.length ? `<dt>Open bugs</dt><dd>${openBugs.map(caseLink).join("<br>")}</dd>` : ""}
        ${(st.fixed_bugs || []).length ? `<dt>Fixed</dt><dd>${st.fixed_bugs.map(caseLink).join("<br>")}</dd>` : ""}
        ${notes.length ? `<dt>Learned</dt><dd>${notes.map((p) => esc(`${(KIND_TEXT[p.kind] || (() => p.kind))(p)}: ${p.why}`)).join("<br>")}</dd>` : ""}
      </dl>
    </div>`;
  $("#nd-close").addEventListener("click", () => { selectedNode = null; renderMap(data.board); });
  box.querySelectorAll("[data-goto-case]").forEach((btn) => btn.addEventListener("click", () => {
    if (!b.cases[btn.dataset.gotoCase]) return;
    selectedCase = btn.dataset.gotoCase;
    renderCases(b);
    $("#case-list").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

function renderExposure(b) {
  const history = b.map.history || [];
  const box = $("#exposure");
  if (history.length < 2) { box.innerHTML = ""; return; }
  const W = 360, H = 120, PAD = { l: 22, r: 6, t: 10, b: 20 };
  const t0 = history[0].ts, t1 = Math.max(now(), history[history.length - 1].ts);
  const max = Math.max(1, ...history.map((h) => h.reached));
  const x = (ts) => PAD.l + ((ts - t0) / Math.max(1, t1 - t0)) * (W - PAD.l - PAD.r);
  const y = (v) => PAD.t + (1 - v / max) * (H - PAD.t - PAD.b);
  let d = `M${x(history[0].ts)},${y(history[0].reached)}`;
  history.slice(1).forEach((h) => { d += ` H${x(h.ts)} V${y(h.reached)}`; });
  d += ` H${x(t1)}`;
  const last = history[history.length - 1];
  const breaches = history.filter((h) => h.breaches);
  box.innerHTML = `
    <div class="exposure">
      <div class="exp-head"><b>Exposure over time</b><span class="muted">${last.reached} component${last.reached === 1 ? "" : "s"} reachable now</span></div>
      <div class="exp-plot">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Components an attacker can reach, over time">
          <line class="exp-grid" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(0)}" y2="${y(0)}"/>
          <line class="exp-grid" x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(max)}" y2="${y(max)}"/>
          <text class="exp-axis" x="${PAD.l - 6}" y="${y(max) + 3}" text-anchor="end">${max}</text>
          <text class="exp-axis" x="${PAD.l - 6}" y="${y(0) + 3}" text-anchor="end">0</text>
          <path class="exp-line" d="${d}"/>
          ${breaches.map((h) => `<circle class="exp-breach" cx="${x(h.ts)}" cy="${y(h.reached)}" r="4"/>`).join("")}
          <line class="exp-cross hidden" id="exp-cross" y1="${PAD.t}" y2="${H - PAD.b}" x1="0" x2="0"/>
          <text class="exp-axis" x="${PAD.l}" y="${H - 4}">${esc(ago(t0))}</text>
          <text class="exp-axis" x="${W - PAD.r}" y="${H - 4}" text-anchor="end">now</text>
        </svg>
        <div class="exp-tip hidden" id="exp-tip" role="status"></div>
      </div>
      <small class="muted">Components an attacker can reach through open bugs. <span class="exp-key"><i></i>a path reached an asset</span></small>
    </div>`;
  const plot = $(".exp-plot", box), svgEl = $("svg", plot), tip = $("#exp-tip"), cross = $("#exp-cross");
  plot.addEventListener("mousemove", (e) => {
    const r = svgEl.getBoundingClientRect();
    const vx = ((e.clientX - r.left) / r.width) * W;
    let h = history[0];
    for (const item of history) if (x(item.ts) <= vx) h = item;
    cross.setAttribute("x1", x(h.ts)); cross.setAttribute("x2", x(h.ts));
    cross.classList.remove("hidden");
    tip.innerHTML = `<b>${new Date(h.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</b>
      ${h.reached} reachable · ${h.open} open bug${h.open === 1 ? "" : "s"}${h.breaches ? ` · <span class="bad">${h.breaches} breach path${h.breaches > 1 ? "s" : ""}</span>` : ""}`;
    tip.classList.remove("hidden");
    tip.style.left = `${Math.min(Math.max(0, e.clientX - r.left + 12), r.width - 180)}px`;
  });
  plot.addEventListener("mouseleave", () => { tip.classList.add("hidden"); cross.classList.add("hidden"); });
}

function renderActivity(b) {
  const list = $("#activity");
  list.innerHTML = [...b.activity].reverse().map((m) =>
    `<li><b>${esc(m.bot)}</b><span>${esc(m.text)} <small class="muted">${esc(ago(m.ts))}</small></span></li>`).join("")
    || `<li class="muted">Nothing yet.</li>`;
}

// ---- actions ----------------------------------------------------------------------------

async function action(name, body) {
  try {
    await api(`/api/repos/${current}/actions/${name}`, { method: "POST", body: JSON.stringify(body || {}) });
    toast({ sweep: "Full sweep requested", patrol: "Patrol requested", map: "New map draft requested", fix: "Asked the crew to fix it", learn: "Asked the crew to propose a map update" }[name]);
  } catch (err) {
    toast(err.message);
  }
}

async function disconnect() {
  if (!confirm(`Disconnect ${current}? Its token and history are removed from this dashboard. Nothing changes on GitHub.`)) return;
  await api(`/api/repos/${current}`, { method: "DELETE" });
  location.hash = "#/";
}

async function showHook() {
  try {
    const info = await api(`/api/repos/${current}/hook`);
    $("#hook-body").innerHTML = `
      <dt>Payload URL</dt><dd>&lt;your public dashboard URL&gt;${esc(info.path)}</dd>
      <dt>Content type</dt><dd>${esc(info.content_type)}</dd>
      <dt>Secret</dt><dd>${esc(info.secret)}</dd>
      <dt>Events</dt><dd>Just the push event</dd>`;
    $("#hook").showModal();
  } catch (err) {
    toast(err.message);
  }
}

async function openSettings() {
  try {
    const s = await api("/api/settings");
    const form = $("#settings-form");
    form.crew_webhook_url.value = s.crew_webhook_url;
    form.crew_webhook_auth.placeholder = s.crew_webhook_auth_set ? "Set. Leave blank to keep it" : "The routine's Authorization header value";
    form.crew_webhook_auth.value = "";
    form.ingest_token.value = s.ingest_token;
    $("#settings").showModal();
  } catch (err) {
    toast(err.message);
  }
}

$("#settings").addEventListener("close", async () => {
  if ($("#settings").returnValue !== "save") return;
  const form = $("#settings-form");
  try {
    await api("/api/settings", { method: "PUT", body: JSON.stringify({ crew_webhook_url: form.crew_webhook_url.value, crew_webhook_auth: form.crew_webhook_auth.value }) });
    toast("Settings saved");
  } catch (err) {
    toast(err.message);
  }
});
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-copy]");
  if (!btn) return;
  const input = $(`[name="${btn.dataset.copy}"]`);
  navigator.clipboard?.writeText(input.value).then(() => toast("Copied"));
});
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-copy-text]");
  if (!btn) return;
  navigator.clipboard?.writeText(copyBlock.texts[btn.dataset.copyText] || "").then(() => toast("Copied"));
});
$("#open-settings").addEventListener("click", openSettings);

// ---- live updates -----------------------------------------------------------------------

let refreshTimer = null;
function connectStream() {
  const es = new EventSource("/api/stream");
  es.onmessage = (e) => {
    const event = JSON.parse(e.data);
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      repos = await api("/api/repos").catch(() => repos);
      renderSwitch();
      if (event.repo === current) {
        data = await api(`/api/repos/${current}`);
        if (event.type === "pr_opened" || event.type === "bug_confirmed") selectedCase = event.data.bug_id || selectedCase;
        renderRepo();
      }
    }, 150);
  };
  es.onerror = () => { es.close(); setTimeout(connectStream, 2000); };
}

window.addEventListener("hashchange", route);
setInterval(tick, 1000);
route();
connectStream();
