"""orch continue-gate: may the planner implement the first steps itself (SendMessage) instead of a fresh implementer?

The planner proposes with the plan Summary's `continue:` line; this command decides. It measures the planner's
context from its transcript (the last request's prompt + output, which equals the completion notice's token
count), compares tiers, and journals a `continue_gate` line. Any failed or unknown condition is a split, which
is the ordinary path. The rules live in TIERS.md § Planner continuation. Python 3.9 compatible, standard library
only."""
import glob
import json
import os
import re

from . import cli
from . import flow
from . import journal as jr
from . import plan as pl
from . import status as st
from . import usage
from .paths import OrchError, TaskLock, now, require_write_allowed, task_names

CTX_MAX = 200_000   # tokens: a planner context at or above this splits (the only place the number lives)
STEP_MAX = 3        # most steps one continuation may run
EFFORT_GAP = 1      # the planner's effort may exceed the routed effort by 0..EFFORT_GAP levels
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SIGNALS = ("boundary", "sensitive", "parallel")

AGENT_ID_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
TIER_RE = re.compile(r"^([a-z0-9.-]+)/([a-z0-9]+)$")
ITEM = r"[A-Za-z]{0,2}\d+(?:\.\d+)?[a-z]?(?![A-Za-z0-9])"
ITEM_RE = re.compile(ITEM)
STEPS_RE = re.compile(r"^steps?\s+(" + ITEM + r"(?:\s*(?:,|&|\band\b|[–—-])\s*(?:steps?\s+)?" + ITEM + r")*)", re.I)
RANGE_SEP_RE = re.compile(r"^\s*[–—-]\s*$")
CONT_RE = re.compile(r"^\s*[-*]\s*continue\s*:\s*(.*?)\s*$", re.I)
NO_RE = re.compile(r"^no\b", re.I)
DATE_RE = re.compile(r"\d{4}-\d\d-\d\d(?:T\d\d:\d\dZ?)?")
NONE_RE = re.compile(r"^(?:none|n/a|-|—)(?:\W|$)", re.I)
SYNTHETIC = "<synthetic>"


# -- context -------------------------------------------------------------------------------------

def transcript_ctx(agent_id, home):
    """The planner's context size from its subagent transcript: the last request's input + cache writes + cache
    reads + output. None when there is not exactly one transcript or no usable request. Lines that do not parse
    are skipped; nothing outside <home>/projects is read."""
    root = os.path.realpath(os.path.join(home, "projects"))
    pattern = os.path.join(glob.escape(home), "projects", "*", "*", "subagents", "agent-%s.jsonl" % agent_id)
    hits = [p for p in glob.glob(pattern) if os.path.realpath(p).startswith(root + os.sep)]
    if len(hits) != 1:
        return None
    reqs = {}
    try:
        with open(hits[0], encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    usage._parse_line(line, hits[0], 0, None, reqs)
                except Exception:
                    continue
    except OSError:
        return None
    best = None
    for r in reqs.values():
        if r.model == SYNTHETIC:
            continue
        if best is None or r.t >= best.t:
            best = r
    if best is None:
        return None
    total = best.i + best.w5 + best.w1 + best.rd + best.o
    return total if total > 0 else None


# -- plan and status -----------------------------------------------------------------------------

def plan_says(plan):
    """The Summary's `continue:` value, or None when the line is missing."""
    sec = plan.find("summary")
    if sec is None:
        return None
    for i in range(sec.start + 1, sec.end):
        if plan.mask[i]:
            continue
        m = CONT_RE.match(plan.lines[i])
        if m:
            return m.group(1)
    return None


def parse_run(says, ids):
    """`steps 1, 2` / `steps 1–2` -> the step ids named (ranges expand over the plan's order); None when the
    value is missing, `no…`, or unparsable."""
    if says is None or NO_RE.match(says.strip()):
        return None
    m = STEPS_RE.match(says.strip())
    if not m:
        return None
    text = m.group(1)
    found = [(x.start(), x.end(), x.group(0)) for x in ITEM_RE.finditer(text)]
    byname = dict((i.lower(), i) for i in ids)
    named = [byname.get(tok.lower(), tok) for _, _, tok in found]
    out = []

    def add(x):
        if x not in out:
            out.append(x)

    for k, tok in enumerate(named):
        if k > 0 and RANGE_SEP_RE.match(text[found[k - 1][1]:found[k][0]]) and named[k - 1] in ids and tok in ids:
            lo, hi = ids.index(named[k - 1]), ids.index(tok)
            if lo <= hi:
                for x in ids[lo:hi + 1]:
                    add(x)
                continue
        add(tok)
    return out


def done_ids(status, ids):
    """Plan ids up to and including the furthest one an implementer row's `last completed step` names."""
    best = -1
    byname = dict((i.lower(), k) for k, i in enumerate(ids))
    for role, row in status.roles():
        if not role.lower().startswith("implementer"):
            continue
        for tok in ITEM_RE.findall(DATE_RE.sub(" ", row.get("step") or "")):
            best = max(best, byname.get(tok.lower(), -1))
    return ids[:best + 1]


def field_is_none(step, name):
    v = step.field_text(name)
    return v is None or not v.strip() or bool(NONE_RE.match(v.strip()))


# -- the gate ------------------------------------------------------------------------------------

def parse_pairs(text, what, value_re=None):
    """'1=a,2=b' -> [(key, value)]; OrchError on anything else."""
    out = []
    for item in [x.strip() for x in (text or "").split(",") if x.strip()]:
        key, eq, val = item.partition("=")
        key, val = key.strip(), val.strip()
        if not eq or not key or not val or (value_re is not None and not value_re.match(val)):
            raise OrchError("bad %s item %r (expected <step>=<value>)" % (what, item), 1)
        out.append((key, val))
    return out


def evaluate(plan, status, ctx, planner_tier, routed, signals):
    """-> dict(decision, steps, reasons, says, routed). `routed`: {step: tier}; `signals`: {step: [name]}."""
    ids = plan.ids()
    byname = dict((i.lower(), i) for i in ids)
    says = plan_says(plan)
    run = parse_run(says, ids)
    reasons = []
    if run is None:
        reasons.append("plan_no")
        run = []
    else:
        remaining = [i for i in ids if i not in done_ids(status, ids)]
        if run != remaining[:len(run)]:
            reasons.append("not_first")
        if len(run) > STEP_MAX:
            reasons.append("too_many")
    steps = [plan.step(i) for i in run]
    if any(s is not None and (s.field_text("size") or "").strip().upper().startswith("L") for s in steps):
        reasons.append("size_L")
    if ctx is None:
        reasons.append("ctx_unknown")
    elif ctx >= CTX_MAX:
        reasons.append("ctx_high")
    pm, pe = TIER_RE.match(planner_tier).groups()
    rmap = dict((byname.get(k.lower(), k), v) for k, v in routed.items())
    smap = {}
    for k, names in signals.items():
        smap.setdefault(byname.get(k.lower(), k), []).extend(names)
    model_bad = effort_bad = False
    for sid in run:
        tier = rmap.get(sid)
        m = TIER_RE.match(tier) if tier else None
        if not m or m.group(1) != pm:
            model_bad = True
        elif pe not in EFFORTS or m.group(2) not in EFFORTS or \
                not 0 <= EFFORTS.index(pe) - EFFORTS.index(m.group(2)) <= EFFORT_GAP:
            effort_bad = True
    if model_bad:
        reasons.append("tier_model")
    if effort_bad:
        reasons.append("tier_effort")
    if any(s is not None and not field_is_none(s, "visual") for s in steps):
        reasons.append("visual")
    if any(s is not None and not field_is_none(s, "pilot") for s in steps):
        reasons.append("pilot")
    if any(s is not None and re.search(r"\.ipynb\b", s.field_text("files") or "", re.I) for s in steps):
        reasons.append("notebook")
    for name in SIGNALS:
        if any(name in smap.get(sid, []) for sid in run):
            reasons.append("signal:%s" % name)
    if (status.get("mode") or "") != "standard":
        reasons.append("mode")
    return {"decision": "split" if reasons else "continue", "steps": run, "reasons": reasons, "says": says,
            "routed": ["%s=%s" % (sid, rmap[sid]) for sid in run if sid in rmap]}


def cmd_continue_gate(ns, extra):
    task_dir = cli.get_task(ns)
    if not AGENT_ID_RE.match(ns.agent_id):
        raise OrchError("bad --agent-id %r (6-64 letters and digits)" % ns.agent_id, 1)
    if not TIER_RE.match(ns.planner_tier):
        raise OrchError("--planner-tier must look like opus/high, got %r" % ns.planner_tier, 1)
    if ns.tokens is not None and ns.tokens < 0:
        raise OrchError("--tokens must be >= 0", 1)
    routed = {}
    for k, v in parse_pairs(ns.routed, "--routed", TIER_RE):
        if k in routed:
            raise OrchError("duplicate step %r in --routed" % k, 1)
        routed[k] = v
    signals = {}
    for k, v in parse_pairs(ns.signals, "--signals", re.compile("^(?:%s)$" % "|".join(SIGNALS))):
        signals.setdefault(k, []).append(v)
    if not ns.dry_run:
        require_write_allowed()
    status = cli.load_status(task_dir)
    plan = pl.parse_file(ns.plan or os.path.join(task_dir, "plan.md"))
    ctx, src = transcript_ctx(ns.agent_id, usage.home_dir()), "transcript"
    if ctx is None:
        ctx, src = (ns.tokens, "notice") if ns.tokens is not None else (None, "none")
    res = evaluate(plan, status, ctx, ns.planner_tier, routed, signals)
    ids = [int(x) if x.isdigit() else x for x in res["steps"]]
    line = "continue-gate: %s steps=%s ctx=%s (%s) reasons=%s" % (
        res["decision"], ",".join(res["steps"]) or "-", "unknown" if ctx is None else ctx, src,
        ",".join(res["reasons"]) or "-")
    if not ns.dry_run:
        ts = now()
        repo, slug = task_names(task_dir)
        schema = jr.load_schema()
        with TaskLock(task_dir):
            program, auto_ms = flow.program_of(flow.status_or_none(task_dir))
            pairs = [("decision", "=", res["decision"]), ("agent_id", "=", ns.agent_id),
                     ("ctx", ":=", json.dumps(ctx)), ("ctx_src", "=", src), ("planner_tier", "=", ns.planner_tier),
                     ("routed", ":=", json.dumps(res["routed"])), ("steps", ":=", json.dumps(ids)),
                     ("reasons", ":=", json.dumps(res["reasons"])), ("plan_says", ":=", json.dumps(res["says"]))]
            rec, _ = jr.build_record(schema, "continue_gate", pairs, ts, repo, slug, program, auto_ms)
            jr.append(os.path.join(task_dir, "journal.jsonl"), jr.encode(rec))
            cli.append_log(task_dir, cli.log_line(ts, "orchestrator", line))
    print(line)
    return 0


def register(add):
    sp = add("continue-gate", cmd_continue_gate,
             "may the planner implement the first steps itself? (journals continue_gate)")
    sp.add_argument("--agent-id", dest="agent_id", required=True, help="the planner's agent id (transcript lookup)")
    sp.add_argument("--planner-tier", dest="planner_tier", required=True, help="<model>/<effort> the planner ran at")
    sp.add_argument("--routed", default="", help="per-step routed tiers: 1=opus/medium,2=opus/medium")
    sp.add_argument("--signals", default="", help="per-step judgements: 2=boundary,3=parallel (also sensitive)")
    sp.add_argument("--tokens", type=int, default=None,
                    help="context from the completion notice, used when no transcript is found "
                         "(split at %s tokens or more)" % format(CTX_MAX, ","))
    sp.add_argument("--plan", default=None, help="plan.md path (default: <task>/plan.md)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true", help="print the verdict, write nothing")
