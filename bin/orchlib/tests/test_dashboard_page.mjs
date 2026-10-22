// Dashboard page (step 11). Run: node --test test_dashboard_page.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const htmlPath = path.join(here, "..", "dashboard.html");
const html = fs.readFileSync(htmlPath, "utf8");
const golden = JSON.parse(fs.readFileSync(path.join(here, "fixtures", "dash", "expected.json"), "utf8"));
const viewGolden = JSON.parse(fs.readFileSync(path.join(here, "fixtures", "dash", "view.json"), "utf8"));
const script = /<script>([\s\S]*?)<\/script>/.exec(html)[1];

// -- a minimal DOM ---------------------------------------------------------------------------------

class El {
  constructor(doc, tag) {
    this.ownerDocument = doc; this.tag = tag; this.attrs = {}; this.children = [];
    this.parent = null; this.html = ""; this.writes = 0; this.hidden = false; this.textContent = "";
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  set innerHTML(v) { this.html = v; this.writes += 1; }
  get innerHTML() { return this.html; }
  removeChild(n) {
    const i = this.children.indexOf(n);
    assert.notEqual(i, -1, "removeChild of a node that is not a child");
    this.children.splice(i, 1); n.parent = null; return n;
  }
  insertBefore(n, ref) {
    if (n.parent) { n.parent.removeChild(n); }
    const i = ref ? this.children.indexOf(ref) : this.children.length;
    assert.notEqual(i, -1, "insertBefore with a reference that is not a child");
    this.children.splice(i, 0, n); n.parent = this; return n;
  }
}
class Doc {
  constructor() { this.byId = {}; this.title = "Orchestration"; }
  createElement(tag) { return new El(this, tag); }
  getElementById(id) { return this.byId[id] || (this.byId[id] = new El(this, "div")); }
}

function load(extra = {}) {
  const ctx = { module: { exports: {} }, console, ...extra };
  vm.runInNewContext(script, ctx);
  return { api: ctx.module.exports, ctx };
}
const { api } = load();
const j = (v) => JSON.stringify(v);

function key(list, k) { return list.find((p) => p[0] === k)[1]; }

// -- view ------------------------------------------------------------------------------------------

test("view() on the golden data equals the stored html strings", () => {
  assert.equal(j(api.view(golden)), j(viewGolden));
});

test("view() keys: questions, tasks head, one per active task, events, finished", () => {
  const keys = api.view(golden).map((p) => p[0]);
  assert.equal(j(keys), j(["questions", "tasks", ...golden.tasks.map((t) => "task:" + t.id), "events", "finished"]));
});

test("task block shows the words, steps, percent, milestone, tokens, age and questions", () => {
  const list = api.view(golden);
  const a = key(list, "task:myapp/add-login");
  for (const want of ["myapp/add-login", "Building", "Step 2 of 4", "50% done", "Tokens reported by agents: 100000",
    "Last update 30 min ago", "Open questions: 1", "Now: implementer: working on step 3"]) {
    assert.ok(a.includes(want), want + " in " + a);
  }
  assert.ok(!a.includes("Milestone"));
  const p = key(list, "task:My App/fix-menu");
  assert.ok(p.includes("Milestone 1 of 3"), p);
  assert.ok(p.includes("Step 1 of 3") && p.includes("33% done"), p);
  assert.ok(key(list, "task:myapp/paused-job").includes("Paused: usage limit"));
  assert.ok(key(list, "task:myapp/mystery").includes("Unknown state: unknown"));
  assert.ok(key(list, "task:myapp/mystery").includes("No steps yet"));
  assert.ok(key(list, "task:myapp/mystery").includes("Tokens reported by agents: none yet"));
});

test("waiting block lists open questions and plans waiting for approval", () => {
  const w = key(api.view(golden), "questions");
  assert.ok(w.includes("<h2>Waiting for you</h2>"));
  assert.ok(w.includes("Use a session cookie or a token?") && w.includes("(holds up: 3)"));
  assert.ok(w.includes("Waiting for your approval"));
});

test("events and finished blocks", () => {
  const list = api.view(golden);
  const e = key(list, "events");
  assert.ok(e.includes("Merged 0123456 into main") && e.includes("Paused for usage (5h 96%)"));
  const f = key(list, "finished");
  assert.ok(f.includes("<h3>Merged</h3>") && f.includes("<h3>Abandoned</h3>") && f.includes("myapp/dropped"));
});

test("empty data shows the three empty states and does not throw on nulls", () => {
  const empty = { tasks: [], events: [], finished: { merged: [], abandoned: [] } };
  const text = api.view(empty).map((p) => p[1]).join("");
  for (const want of ["Nothing is waiting for you.", "No active tasks.", "No events yet."]) {
    assert.ok(text.includes(want), want);
  }
  const nulls = { id: "r/s", state: "implementing", state_words: "Building", steps_done: null, steps_total: null, percent: null,
    milestones_done: null, milestones_total: null, tokens_reported: null, updated_age_min: null, usage_last: null,
    questions_open: 0, questions: [], current: null, last_log: null, roles: [] };
  const out = api.view({ tasks: [nulls], events: [], finished: { merged: [], abandoned: [] } });
  const t = key(out, "task:r/s");
  assert.ok(t.includes("No steps yet") && t.includes("Last update unknown") && !t.includes("null") && !t.includes("undefined"), t);
  assert.ok(!t.includes("<progress"));
  const half = Object.assign({}, nulls, { steps_total: 5, steps_done: null });
  assert.ok(key(api.view({ tasks: [half], events: [], finished: { merged: [], abandoned: [] } }), "task:r/s").includes("Step ? of 5"));
});

// -- escaping --------------------------------------------------------------------------------------

test("esc() escapes & < > \" ' and nothing else; null is empty", () => {
  assert.equal(api.esc(`&<>"'`), "&amp;&lt;&gt;&quot;&#39;");
  assert.equal(api.esc("plain text 1/2"), "plain text 1/2");
  assert.equal(api.esc(null), "");
  assert.equal(api.esc(undefined), "");
  assert.equal(api.esc(0), "0");
  assert.equal(api.esc("&amp;"), "&amp;amp;");
});

test("a <script> note renders escaped everywhere", () => {
  const list = api.view(golden);
  const all = list.map((p) => p[1]).join("\n");
  assert.ok(all.includes("&lt;script&gt;alert(2)&lt;/script&gt; ok?"), "question text");
  assert.ok(all.includes("&lt;script&gt;alert(&quot;log&quot;)&lt;/script&gt; plan ready"), "log line");
  assert.ok(all.includes("&lt;b&gt;planner&lt;/b&gt; finished: &lt;img src=x onerror=alert(1)&gt;"), "event words");
  for (const [, h] of list) {
    assert.ok(!/<script/i.test(h) && !/<img/i.test(h) && !/<b>/i.test(h), h.slice(0, 80));
  }
});

test("hostile values in every field cannot open a tag or an attribute", () => {
  const evil = `"><img src=x onerror=alert(1)>'`;
  const t = { id: evil, state: "awaiting_approval", state_words: evil, steps_total: 3, steps_done: 1, percent: evil,
    milestones_total: 2, milestones_done: evil, tokens_reported: evil, updated_age_min: evil, usage_last: { "5h": evil, "7d": evil },
    questions_open: evil, questions: [{ id: evil, text: evil, blocks: evil }], current: evil, last_log: evil, roles: [] };
  const data = { tasks: [t], events: [{ ts: evil, id: evil, kind: "x", words: evil }],
    finished: { merged: [{ id: evil, ended: evil }], abandoned: [{ id: evil, ended: evil }] } };
  const list = api.view(data);
  for (const [, h] of list) {
    assert.ok(!/<img/i.test(h), h.slice(0, 120));
    assert.ok(!/value="[^"]*"[^>]*onerror/i.test(h));
  }
  // the one attribute built from data, <progress value=…>, stays inside its quotes
  const prog = /<progress max="100" value="([^"]*)"><\/progress>/.exec(key(list, "task:" + evil));
  assert.ok(prog && !prog[1].includes('"') && !prog[1].includes("<"), key(list, "task:" + evil));
});

// -- apply -----------------------------------------------------------------------------------------

function container() {
  const doc = new Doc();
  return new El(doc, "main");
}

test("apply() keeps node identity for unchanged keys and touches only changed ones", () => {
  const c = container();
  const base = [["a", "<p>A</p>"], ["b", "<p>B</p>"], ["c", "<p>C</p>"]];
  api.apply(c, base);
  assert.equal(c.children.length, 3);
  const nodes = c.children.slice();
  assert.equal(j(c.children.map((n) => n.getAttribute("data-key"))), j(["a", "b", "c"]));
  assert.equal(j(nodes.map((n) => n.html)), j(["<p>A</p>", "<p>B</p>", "<p>C</p>"]));
  assert.equal(c.writes, 0, "the container's own html is never written");

  api.apply(c, base);                        // nothing changed: nothing written, same nodes
  assert.ok(c.children.every((n, i) => n === nodes[i]));
  assert.equal(j(nodes.map((n) => n.writes)), j([1, 1, 1]));

  api.apply(c, [["a", "<p>A</p>"], ["b", "<p>B2</p>"], ["c", "<p>C</p>"]]);   // one changed
  assert.ok(c.children.every((n, i) => n === nodes[i]));
  assert.equal(j(nodes.map((n) => n.writes)), j([1, 2, 1]));
  assert.equal(nodes[1].html, "<p>B2</p>");

  api.apply(c, [["c", "<p>C</p>"], ["a", "<p>A</p>"], ["b", "<p>B2</p>"]]);   // reordered: same nodes, no writes
  assert.equal(j(c.children.map((n) => n.getAttribute("data-key"))), j(["c", "a", "b"]));
  assert.equal(c.children[0], nodes[2]); assert.equal(c.children[1], nodes[0]); assert.equal(c.children[2], nodes[1]);
  assert.equal(j(nodes.map((n) => n.writes)), j([1, 2, 1]));

  api.apply(c, [["c", "<p>C</p>"], ["new", "<p>N</p>"], ["b", "<p>B2</p>"]]);  // a removed, one inserted in the middle
  assert.equal(j(c.children.map((n) => n.getAttribute("data-key"))), j(["c", "new", "b"]));
  assert.equal(c.children[0], nodes[2]); assert.equal(c.children[2], nodes[1]);
  assert.equal(nodes[0].parent, null);
  assert.equal(c.children[1].html, "<p>N</p>");
  assert.equal(c.writes, 0);
});

test("apply() compares against what it last wrote, not against the browser's re-serialised html", () => {
  const c = container();
  api.apply(c, [["a", "<p>A &amp; B</p>"]]);
  const n = c.children[0];
  n.html = "<p>A &amp;amp; B</p>";            // what a browser may hand back
  api.apply(c, [["a", "<p>A &amp; B</p>"]]);
  assert.equal(n.writes, 1);
});

test("apply() with duplicate or odd keys keeps the container consistent", () => {
  const c = container();
  api.apply(c, [["__proto__", "<p>x</p>"], ["constructor", "<p>y</p>"], ["task:a/b", "<p>z</p>"]]);
  assert.equal(j(c.children.map((n) => n.getAttribute("data-key"))), j(["__proto__", "constructor", "task:a/b"]));
  api.apply(c, [["task:a/b", "<p>z</p>"]]);
  assert.equal(c.children.length, 1);
  api.apply(c, []);
  assert.equal(c.children.length, 0);
});

test("apply() on the golden data twice redraws nothing", () => {
  const c = container();
  api.apply(c, api.view(golden));
  const writes = c.children.map((n) => n.writes);
  api.apply(c, api.view(golden));
  assert.equal(j(c.children.map((n) => n.writes)), j(writes));
  assert.ok(writes.every((w) => w === 1));
});

// -- polling ---------------------------------------------------------------------------------------

async function settle() { for (let i = 0; i < 10; i++) { await new Promise((r) => setImmediate(r)); } }

function boot(responses) {
  const doc = new Doc();
  const timers = [];
  const reloads = [];
  const calls = [];
  const queue = responses.slice();
  const fetch = (url, opts) => {
    calls.push([url, opts]);
    const next = queue.length > 1 ? queue.shift() : queue[0];
    return next instanceof Error ? Promise.reject(next)
      : Promise.resolve({ ok: next.ok !== false, status: next.status || 200, json: () => Promise.resolve(next.data) });
  };
  const ctx = { document: doc, fetch, setTimeout: (f, ms) => { timers.push([f, ms]); }, location: { reload: () => reloads.push(1) },
    console: { error() {}, log() {} }, module: { exports: {} } };
  vm.runInNewContext(script, ctx);
  return { doc, timers, reloads, calls };
}

test("polls dashboard.json with no-store, redraws in place, and schedules the next poll in 3000 ms", async () => {
  const env = boot([{ data: { ...golden, page_version: "v1" } }]);
  await settle();
  assert.equal(j(env.calls[0]), j(["dashboard.json", { cache: "no-store" }]));
  const app = env.doc.getElementById("app");
  assert.equal(app.children.length, 2 + golden.tasks.length + 2);
  assert.equal(env.doc.getElementById("banner").hidden, true);
  assert.equal(env.doc.getElementById("title").textContent, "Orchestration");
  assert.equal(j(env.timers.map((t) => t[1])), j([3000]));
  const first = app.children.slice();
  env.timers.shift()[0]();                  // next poll, same data: same nodes, nothing rewritten
  await settle();
  assert.ok(app.children.every((n, i) => n === first[i]));
  assert.ok(app.children.every((n) => n.writes === 1));
  assert.equal(env.reloads.length, 0);
});

test("a failed fetch shows the banner and retries in 5000 ms; the next success hides it", async () => {
  const env = boot([new Error("down"), { ok: false, status: 500, data: {} }, { data: { ...golden, page_version: "v1" } }]);
  await settle();
  const banner = env.doc.getElementById("banner");
  assert.equal(banner.hidden, false);
  assert.equal(j(env.timers.map((t) => t[1])), j([5000]));
  env.timers.shift()[0]();                  // HTTP 500 counts as a failure too
  await settle();
  assert.equal(banner.hidden, false);
  assert.equal(env.timers[0][1], 5000);
  env.timers.shift()[0]();
  await settle();
  assert.equal(banner.hidden, true);
  assert.equal(env.timers[0][1], 3000);
});

test("only a changed page_version reloads the page", async () => {
  const env = boot([{ data: { ...golden, page_version: "v1" } }, { data: { ...golden, page_version: "v1" } },
    { data: { ...golden, page_version: "v2" } }]);
  await settle();
  env.timers.shift()[0]();
  await settle();
  assert.equal(env.reloads.length, 0);
  env.timers.shift()[0]();
  await settle();
  assert.equal(env.reloads.length, 1);
});

test("a data error while drawing does not show the unreachable banner and retries in 5000 ms", async () => {
  const bad = { tasks: null, events: [], finished: { merged: [], abandoned: [] }, page_version: "v1" };
  const env = boot([{ data: bad }]);
  await settle();
  assert.equal(env.doc.getElementById("banner").hidden, true);
  assert.equal(env.timers[0][1], 5000);
});

// -- static checks ---------------------------------------------------------------------------------

function count(text, needle) { return text.split(needle).length - 1; }
const lines = html.split("\n");

test("font lines: exact, once each; no other word or URL for the font", () => {
  const family = `  --font: system-ui, -apple-system, "Segoe UI", sans-serif;`;
  assert.equal(lines.filter((l) => l === family).length, 1, family);
  assert.equal(lines.filter((l) => l.includes("<link")).length, 0);
  assert.equal(count(html, "font-family"), 1);
  assert.ok(html.includes("font-family: var(--font);"));
});

test("no other external resource", () => {
  assert.ok(!/<script[^>]*\ssrc=/i.test(html), "script src");
  for (const bad of ["@import", "url(", "<img", "<iframe", "<form", "<object", "<embed", "<base", "<audio", "<video"]) {
    assert.ok(!html.includes(bad), bad);
  }
  assert.equal(count(html, "<link"), 0);
  assert.equal(count(html, "<script"), 1);
  const urls = html.match(/https?:\/\/[^\s"'<>)]+/g) || [];
  assert.equal(urls.length, 0, urls.join(" "));
  assert.ok(!/\son[a-z]+\s*=/i.test(html.replace(script, "")), "inline event handler");
});

test("exact texts appear once each", () => {
  const once = (needle) => assert.equal(count(html, needle), 1, needle);
  once("<title>Orchestration</title>");
  assert.equal(count(html, "Orchestration"), 1);
  for (const h of ["Waiting for you", "Active tasks", "Recent events", "Finished"]) { once(`<h2>${h}</h2>`); }
  for (const s of ["Nothing is waiting for you.", "No active tasks.", "No events yet.", "No steps yet",
    "Can't reach the dashboard server — retrying", `"Tokens reported by agents: "`, `"Last update "`, `" min ago"`,
    `"Open questions: "`, `"Milestone "`, `% done</li>`, `"Step "`]) {
    once(s);
  }
});

test("layout: one column, 60rem, 16px, light and dark", () => {
  assert.equal(count(html, "max-width: 60rem"), 1);
  assert.equal(count(html, "font-size: 16px"), 1);
  assert.equal(count(html, "@media (prefers-color-scheme: dark)"), 1);
  assert.ok(/@media \(prefers-color-scheme: dark\) \{\s*:root \{[^}]*--bg:/.test(html));
  assert.ok(!/\bgrid\b|\bflex\b|float:/.test(html), "no multi-column layout");
});

test("poll 3000 ms, retry 5000 ms, no-store; location.reload only for a changed page_version", () => {
  const delays = [...script.matchAll(/again\((\d+)\)/g)].map((m) => m[1]);
  assert.equal(j([...new Set(delays)].sort()), j(["3000", "5000"]));
  assert.equal(count(script, "again(3000)"), 1);
  assert.equal(count(script, "setTimeout("), 1);
  assert.ok(script.includes('fetch("dashboard.json", { cache: "no-store" })'));
  assert.equal(count(html, "location.reload"), 1);
  const at = script.indexOf("location.reload");
  const before = script.slice(script.lastIndexOf("if (", at), at);
  assert.ok(before.includes("data.page_version !== version"), before);
});
