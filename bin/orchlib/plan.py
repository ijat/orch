"""plan.md parser (tolerant), the mention matcher the briefs use, and `orch plan-check`.

The parser never raises on text: a plan it cannot recognise is `legacy` (no step blocks) and the brief prints
the whole Steps section instead. Headings inside fenced code are ignored. Blocks are kept as line ranges so
a brief can print them byte for byte. Python 3.9 compatible, standard library only."""
import importlib.util
import os
import re
import subprocess
import sys

from .paths import BIN_DIR, OrchError, resolve_task

FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
HEAD_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
ID = r"\d+[a-z]?(?![A-Za-z0-9])"
STEP_HEAD_RE = re.compile(r"^step\s+(\d+[a-z]?)(?![A-Za-z0-9])\s*(?:[—–:.-]+\s*(.*))?$", re.I)
REVISION_RE = re.compile(r"^revision\s+(\d+)\b", re.I)
SEP = r"\s*(?:[,/&–—-]|\band\b|\bor\b)\s*"
LIST = ID + r"(?:" + SEP + r"(?:steps?\s+)?" + ID + r")*"
STEP_REF_RE = re.compile(r"\bsteps?[\s_-]*(" + LIST + r")", re.I)
ID_RE = re.compile(ID, re.I)
FIELD_LINE_RE = re.compile(r"^- ([A-Za-z][A-Za-z ]*?):[ \t]*(.*)$")
KNOWN = ("size", "files", "depends", "context", "do", "contract", "sketch", "guide", "pilot", "visual", "done when",
         "tier", "effort", "tier rationale", "effort rationale", "escalation", "tradeoff")
REQUIRED = ("size", "files", "do", "contract", "done when")
BULLET_RE = re.compile(r"^(?:[-*+]|\d+[.)])[ \t]+")
LITE_OMITTED_RE = re.compile(r"^\s*lite:\s*omitted\b", re.I)
DECISION_RE = re.compile(r"decision|^questions \(decided|\bgate\b")
PATH_TOKEN_RE = re.compile(r"[^\s,;`'\"()<>\[\]{}|]+")
PLACEHOLDER_RE = re.compile(r"^<.*>$")


def norm_title(title):
    t = re.sub(r"\s*\(.*\)\s*$", "", title.strip().lower()).strip()
    return t


class Sub(object):
    """A `###` block inside a `##` section."""

    def __init__(self, title, start, end):
        self.title, self.start, self.end = title, start, end


class Sec(object):
    """A `##` section: lines[start] is the heading, [start + 1, end) the body."""

    def __init__(self, title, start, end):
        self.title, self.start, self.end = title, start, end
        self.norm = norm_title(title)
        self.subs = []
        m = REVISION_RE.match(title.strip())
        self.rev = int(m.group(1)) if m else None

    @property
    def pre_end(self):
        return self.subs[0].start if self.subs else self.end


class Step(object):
    def __init__(self, sid, title, start, end, heading, plan):
        self.id, self.title, self.start, self.end, self.heading = sid, title, start, end, heading
        self.plan = plan
        self.fields = {}      # name -> (first line index, end index exclusive)
        self.order = []

    def field_lines(self, name):
        rng = self.fields.get(name)
        return self.plan.lines[rng[0]:rng[1]] if rng else []

    def field_text(self, name):
        """Value of a field: the rest of its first line plus continuation lines (no `- name:` prefix)."""
        lines = self.field_lines(name)
        if not lines:
            return None
        m = FIELD_LINE_RE.match(lines[0])
        first = m.group(2) if m else lines[0]
        return "\n".join([first] + lines[1:]).strip()

    def text(self):
        return self.plan.block(self.start, self.end)

    def files(self, repo=None):
        """Absolute (or ~/ expanded) paths named in the `files` and `context` fields, in order."""
        out = []
        for name in ("files", "context"):
            for p in path_tokens(self.field_text(name) or "", repo):
                if p not in out:
                    out.append(p)
        return out


class Plan(object):
    def __init__(self, text):
        self.text = text
        self.lines = text.split("\n")
        self.mask = fence_mask(self.lines)
        self.header = {}
        self.sections = []
        self.steps = []           # step blocks of the Steps section(s) (or, with no Steps section, all outside revisions)
        self.rev_steps = []       # (rev number, Step) for `### Step N` blocks inside revisions
        self.extra_steps = []     # first revision block of each id that has no block in the Steps section
        self.step_sections = []
        self.legacy = True
        self._parse()

    # -- parsing ---------------------------------------------------------------------------------

    def _heads(self):
        out = []
        for i, l in enumerate(self.lines):
            if self.mask[i]:
                continue
            m = HEAD_RE.match(l)
            if m:
                out.append((i, len(m.group(1)), m.group(2).strip()))
        return out

    def _parse(self):
        n = len(self.lines)
        heads = self._heads()
        l2 = [h for h in heads if h[1] <= 2]
        first2 = next((h[0] for h in l2 if h[1] == 2), n)
        for i in range(first2):
            m = FIELD_LINE_RE.match(self.lines[i]) if not self.mask[i] else None
            if m and m.group(1).strip().lower() not in self.header:
                self.header[m.group(1).strip().lower()] = m.group(2).strip()
        for k, (i, lvl, title) in enumerate(l2):
            if lvl != 2:
                continue
            end = l2[k + 1][0] if k + 1 < len(l2) else n
            sec = Sec(title, i, end)
            self.sections.append(sec)
        l3 = [h for h in heads if h[1] <= 3]
        for sec in self.sections:
            subs = [h for h in l3 if h[1] == 3 and sec.start < h[0] < sec.end]
            for k, (i, _, title) in enumerate(subs):
                end = subs[k + 1][0] if k + 1 < len(subs) else sec.end
                sec.subs.append(Sub(title, i, end))
        self.step_sections = [s for s in self.sections if s.norm.startswith("steps") and s.rev is None]
        for k, (i, lvl, title) in enumerate(l3):
            if lvl != 3:
                continue
            m = STEP_HEAD_RE.match(title)
            if not m:
                continue
            end = l3[k + 1][0] if k + 1 < len(l3) else n
            step = Step(m.group(1), (m.group(2) or "").strip(), i, end, title, self)
            self._fields(step)
            owner = None
            for sec in self.sections:
                if sec.start < i < sec.end:
                    owner = sec
            if owner is not None and owner.rev is not None:
                self.rev_steps.append((owner.rev, step))
            elif owner in self.step_sections or not self.step_sections:
                self.steps.append(step)
        originals = set(s.id for s in self.steps)
        for _, s in self.rev_steps:
            if s.id not in originals:
                originals.add(s.id)
                self.extra_steps.append(s)
        self.legacy = not self.steps

    def _fields(self, step):
        cur = None
        for i in range(step.start + 1, step.end):
            if not self.mask[i]:
                m = FIELD_LINE_RE.match(self.lines[i])
                if m and m.group(1).strip().lower() in KNOWN:
                    name = m.group(1).strip().lower()
                    if cur:
                        self._close(step, cur, i)
                    cur = [name, i] if name not in step.fields else ["", i]
                    continue
        if cur:
            self._close(step, cur, step.end)

    def _close(self, step, cur, end):
        name, start = cur
        if not name:
            return
        while end > start + 1 and not self.lines[end - 1].strip():
            end -= 1
        step.fields[name] = (start, end)
        step.order.append(name)

    # -- access ----------------------------------------------------------------------------------

    def block(self, a, b):
        """Lines [a, b) as text, trailing blank lines dropped."""
        while b > a and not self.lines[b - 1].strip():
            b -= 1
        return "\n".join(self.lines[a:b])

    def body(self, sec):
        return self.block(sec.start + 1, sec.end).strip("\n")

    def find(self, name):
        """First level-2 section whose normalised title is `name` or starts with it (revisions excluded)."""
        for s in self.sections:
            if s.rev is None and (s.norm == name or s.norm.startswith(name)):
                return s
        return None

    def revisions(self):
        return [s for s in self.sections if s.rev is not None]

    def decision_sections(self):
        return [s for s in self.sections if s.rev is None and DECISION_RE.search(s.norm)]

    def step(self, sid):
        """The step's block in the Steps section; for a step a revision introduced, that revision's block."""
        for s in self.steps + self.extra_steps:
            if s.id == sid:
                return s
        return None

    def ids(self):
        return [s.id for s in self.steps + self.extra_steps]

    def step_count(self):
        return len(self.steps) + len(self.extra_steps)

    @property
    def planned_against(self):
        v = self.header.get("planned against", "")
        return "" if not v or PLACEHOLDER_RE.match(v) else v.split()[0].strip("`")

    def is_omitted(self, sec):
        body = self.body(sec).strip() if sec else ""
        return bool(sec) and bool(LITE_OMITTED_RE.match(body))

    def last_step(self):
        return self.steps[-1].id if self.steps else None


def fence_mask(lines):
    mask = [False] * len(lines)
    fence = None
    for i, l in enumerate(lines):
        m = FENCE_RE.match(l)
        if fence is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence = (m.group(1)[0], len(m.group(1)))
                mask[i] = True
        else:
            mask[i] = True
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
    return mask


def parse_plan(text):
    return Plan(text)


def parse_file(path):
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise OrchError("cannot read %s: %s" % (path, exc), 1)
    return Plan(raw.decode("utf-8", "surrogateescape"))


# -- step references ----------------------------------------------------------------------------

def _num(sid):
    m = re.match(r"^\d+", sid)
    return int(m.group(0)) if m else None


def expand_refs(text):
    """Step ids named in a reference list ("3", "3, 5 and 6", "3–5"). Returns (singles, ranges)."""
    ids = [(m.start(), m.end(), m.group(0).lower()) for m in ID_RE.finditer(text)]
    singles, ranges = set(), []
    for k, (s, e, sid) in enumerate(ids):
        singles.add(sid)
        if k + 1 < len(ids):
            between = text[e:ids[k + 1][0]]
            if re.match(r"^\s*[–—-]\s*$", between):
                lo, hi = _num(sid), _num(ids[k + 1][2])
                if lo is not None and hi is not None and lo <= hi and hi - lo < 200:
                    ranges.append((lo, hi))
    return singles, ranges


def refs_hit(text, wanted):
    """True when `text` has a `step N` / `steps N–M` reference to one of the step ids in `wanted`."""
    for m in STEP_REF_RE.finditer(text):
        singles, ranges = expand_refs(m.group(1))
        for w in wanted:
            if w in singles:
                return True
            n = _num(w)
            if n is not None and any(lo <= n <= hi for lo, hi in ranges):
                return True
    return False


def parse_depends(value):
    """(ids, ranges) named at the front of a `depends` value; `none`/prose/empty -> ([], [])."""
    if value is None or not value.strip():
        return [], []
    first = re.sub(r"^\s*(?:steps?\s+)?", "", value.strip().splitlines()[0], flags=re.I)
    m = re.match(r"^(" + ID + r"(?:" + SEP + r"(?:steps?\s+)?" + ID + r")*)", first, re.I)
    if not m:
        return [], []
    singles, ranges = expand_refs(m.group(1))
    return sorted(singles), ranges


# -- paths ---------------------------------------------------------------------------------------

def path_tokens(text, repo=None):
    """Path-like tokens of a field value: absolute (or ~/), or relative with a `/` resolved against `repo`."""
    out = []
    for tok in PATH_TOKEN_RE.findall(text):
        tok = tok.rstrip(".:")
        if tok.startswith("~/"):
            tok = os.path.expanduser(tok)
        if tok.startswith("/"):
            if len(tok) > 1 and ("/" in tok[1:] or "." in tok):
                out.append(tok)
        elif repo and "/" in tok and not tok.startswith(("http:", "https:")) and re.search(r"[A-Za-z0-9]", tok):
            out.append(os.path.join(repo, tok))
    return out


def relative_to(path, root):
    if not root:
        return None
    root = root.rstrip("/")
    if path.startswith(root + "/"):
        return path[len(root) + 1:]
    return None


class Keys(object):
    """What counts as a mention of a set of steps: `step N` references, the steps' file paths (absolute and
    relative to the repo), and basenames of at least 6 characters that contain a dot."""

    def __init__(self, ids, paths, roots=()):
        self.ids = set(i.lower() for i in ids)
        names = set()
        for p in paths:
            p = p.rstrip("/")
            names.add(p)
            for r in roots:
                rel = relative_to(p, r)
                if rel:
                    names.add(rel)
            base = os.path.basename(p)
            if len(base) >= 6 and "." in base:
                names.add(base)
        names = sorted((n for n in names if n), key=lambda n: (-len(n), n))
        self.re = None
        if names:
            self.re = re.compile(r"(?<![\w.~-])(?:" + "|".join(re.escape(n) for n in names) + r")(?![\w-]|\.\w)", re.I)

    def matches(self, text):
        if self.ids and refs_hit(text, self.ids):
            return True
        return bool(self.re and self.re.search(text))


# -- units (paragraphs and top-level bullets) ----------------------------------------------------

def split_units(lines, mask, a, b):
    """[(start, end)] of paragraphs and top-level bullets (with their indented continuation) in lines[a:b]."""
    units = []
    cur = None      # [start, end, kind]
    i = a
    while i < b:
        l = lines[i]
        if mask[i]:
            if cur is None:
                cur = [i, i + 1, "para"]
            else:
                cur[1] = i + 1
            i += 1
            continue
        if not l.strip():
            if cur is not None and cur[2] == "bullet":
                j = i
                while j < b and not lines[j].strip():
                    j += 1
                if j < b and (lines[j][:1] in (" ", "\t") or mask[j]):
                    cur[1] = j
                    i = j
                    continue
            if cur is not None:
                units.append((cur[0], cur[1]))
                cur = None
            i += 1
            continue
        if BULLET_RE.match(l):
            if cur is not None:
                units.append((cur[0], cur[1]))
            cur = [i, i + 1, "bullet"]
        elif cur is None:
            cur = [i, i + 1, "para"]
        elif cur[2] == "bullet" and l[:1] not in (" ", "\t"):
            units.append((cur[0], cur[1]))
            cur = [i, i + 1, "para"]
        else:
            cur[1] = i + 1
        i += 1
    if cur is not None:
        units.append((cur[0], cur[1]))
    return units


LABEL_RE = re.compile(r"(?<![\w])Steps?\s+(" + LIST + r")\s*:", re.I)


def label_segments(text):
    """Split one bullet that carries several `Step N:` labels into one segment per label; else None."""
    marks = [m.start() for m in LABEL_RE.finditer(text)]
    if len(marks) < 2:
        return None
    return [text[marks[k]:marks[k + 1] if k + 1 < len(marks) else len(text)].strip() for k in range(len(marks))]


def filter_region(plan, a, b, keys, keep_paras=False, split_labels=False):
    """Units of lines[a:b] that mention `keys`, as text. With keep_paras, non-bullet paragraphs stay."""
    out = []
    for s, e in split_units(plan.lines, plan.mask, a, b):
        text = plan.block(s, e)
        if not text.strip():
            continue
        is_bullet = bool(BULLET_RE.match(plan.lines[s])) and not plan.mask[s]
        if keep_paras and not is_bullet:
            out.append(text)
            continue
        if split_labels and is_bullet:
            segs = label_segments(text)
            if segs:
                kept = [("- " + re.sub(r"^[-*+]\s+", "", sg)) for sg in segs if keys.matches(sg)]
                out.extend(kept)
                continue
        if keys.matches(text):
            out.append(text)
    return out


def filter_section(plan, sec, keys, keep_paras=False, split_labels=False):
    """Mentioning units of a `##` section, each `###` heading printed once above its kept units."""
    if sec is None:
        return ""
    parts = []
    pre = filter_region(plan, sec.start + 1, sec.pre_end, keys, keep_paras, split_labels)
    if pre:
        parts.append("\n\n".join(pre))
    for sub in sec.subs:
        kept = filter_region(plan, sub.start + 1, sub.end, keys, keep_paras, split_labels)
        if kept:
            parts.append(plan.lines[sub.start] + "\n\n" + "\n\n".join(kept))
    return "\n\n".join(parts)


# -- plan-check ----------------------------------------------------------------------------------

STANDARD_SECTIONS = (("goal", "Goal"), ("findings", "Findings"), ("decisions", "Decisions"), ("approach", "Approach"),
                     ("files", "Files and areas touched"), ("parallelization", "Parallelization"),
                     ("verification", "Verification"), ("risks", "Risks and unknowns"))
SUMMARY_STEP_LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?(\d+[a-z]?)\.\s+\S")


def check_plan(plan, lite=False):
    """[(level, message)] with level FAIL or WARN."""
    out = []
    summary = plan.find("summary")
    if summary is None:
        out.append(("FAIL", "no Summary section"))
    if not plan.step_sections:
        out.append(("FAIL", "no Steps section"))
    elif not plan.steps:
        out.append(("FAIL", "Steps has no `### Step <id> — <title>` blocks"))
    seen = {}
    for pos, st in enumerate(plan.steps):
        if st.id in seen:
            out.append(("FAIL", "duplicate step id %s (blocks at lines %d and %d)" % (st.id, seen[st.id], st.start + 1)))
        else:
            seen[st.id] = st.start + 1
    order = {}
    for pos, st in enumerate(plan.steps):
        order.setdefault(st.id, pos)
    for pos, st in enumerate(plan.steps):
        missing = [f for f in REQUIRED if st.field_text(f) is None or not st.field_text(f)]
        if missing:
            out.append(("FAIL", "step %s lacks: %s" % (st.id, ", ".join(missing))))
        dep = st.field_text("depends")
        if dep is not None:
            singles, ranges = parse_depends(dep)
            names = list(singles)
            for lo, hi in ranges:
                names.extend(i for i in order if _num(i) is not None and lo <= _num(i) <= hi)
            for d in sorted(set(names)):
                if d not in order:
                    out.append(("FAIL", "step %s depends on unknown step %s" % (st.id, d)))
                elif order[d] >= pos:
                    out.append(("FAIL", "step %s depends on step %s, which is not earlier" % (st.id, d)))
        files = st.field_text("files") or ""
        bad = []
        for tok in PATH_TOKEN_RE.findall(files):
            tok = tok.rstrip(".:")
            if not tok or tok.startswith(("/", "~")) or tok.lower() in ("none", "n/a"):
                continue
            if "/" in tok or re.search(r"\.\w{1,6}$", tok):
                bad.append(tok)
        if bad:
            out.append(("WARN", "step %s: non-absolute path(s) in files: %s" % (st.id, ", ".join(bad[:4]) + (" …" if len(bad) > 4 else ""))))
    if not plan.planned_against:
        out.append(("WARN", "no `planned against:` header (brief freshness checks will say unknown)"))
    if summary is not None and plan.steps:
        indexed = _summary_index(plan, summary)
        if indexed is not None and indexed != len(plan.steps):
            out.append(("WARN", "Summary lists %d step(s), Steps has %d block(s)" % (indexed, len(plan.steps))))
    for key, title in STANDARD_SECTIONS:
        sec = plan.find(key)
        if sec is None:
            out.append(("WARN", "no %s section%s" % (title, " (lite plans write `Lite: omitted.`)" if lite else "")))
        elif plan.is_omitted(sec) and not lite:
            out.append(("WARN", "%s is `Lite: omitted.` in a plan not checked with --lite" % title))
    return out


GUIDE_SECTIONS = ("approach", "checklist", "verify", "do / don't")
GUIDE_WORDS_MAX = 600
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
NEW_MARK_RE = re.compile(r"\b(?:new|add|adds|create[sd]?)\b", re.I)


def _repo_files(root):
    """Tracked (and untracked, unignored) paths under a git root, or a bounded walk; None when root is unusable."""
    if not root or not os.path.isdir(root):
        return None
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            return set(l for l in out.stdout.splitlines() if l)
    except (OSError, subprocess.SubprocessError):
        pass
    found = set()
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not d.startswith(".") and d not in ("node_modules", "target", "build", "dist")]
        for fn in fns:
            found.add(os.path.relpath(os.path.join(dp, fn), root))
            if len(found) > 200000:
                return found
    return found


FILE_EXTS = frozenset("""go mod sum rs toml py pyi ts tsx mts cts js jsx mjs cjs json jsonc md mdx rst txt yaml yml ini cfg
    conf env cs csproj sln fs kt kts java gradle c h cc cpp hpp m mm swift rb erb php sh bash zsh fish ps1 sql proto
    graphql html htm css scss less vue svelte xml plist lock golden snap tmpl tpl j2 dockerfile make mk cmake
    ipynb""".split())


def _cited_path(tok):
    """The file path a backticked token names, or None when it isn't one (commands, flags, symbols, routes, globs)."""
    t = tok.strip()
    if not t or " " in t or "..." in t or t.startswith(("-", "$", "http://", "https://", ".")) \
            or any(c in t for c in "*<>{}|=\"'^$[]()\\"):
        return None
    t = re.sub(r"(?::\d+(?:-\d+)?|::?[A-Za-z_][\w.]*)$", "", t).rstrip(",.;:")
    last = t.rstrip("/").split("/")[-1]
    if t.endswith("/") and "/" in t[:-1].lstrip("/") + "/" and not t.startswith("/"):
        return t
    m = re.search(r"\.([A-Za-z0-9]{1,10})$", last)
    if not m or m.group(1).lower() not in FILE_EXTS or last.startswith("."):
        return None
    if t.startswith("/") and not os.path.isdir("/" + t.lstrip("/").split("/")[0]):
        return None  # a route like /api/v1/users.json, not a file on this machine
    return t


def _path_known(path, files):
    if os.path.isabs(path) or path.startswith("~"):
        return os.path.exists(os.path.expanduser(path))
    p = path.lstrip("./")
    if p.endswith("/"):
        p = p.rstrip("/")
        return any(f == p or f.startswith(p + "/") or ("/" + p + "/") in ("/" + f) for f in files)
    if "/" in p:
        return any(f == p or f.endswith("/" + p) or f.startswith(p + "/") or ("/" + p + "/") in ("/" + f) for f in files)
    return any(os.path.basename(f) == p for f in files)


def check_guides(plan, plan_dir, task_dir=None, roots=()):
    """[(level, message)] for every step with a `guide:` file: exists, four sections, size, cited paths exist."""
    out = []
    files = None
    for r in roots:
        files = _repo_files(r)
        if files is not None:
            break
    for st in plan.steps:
        g = st.field_text("guide")
        if not g or re.match(r"^(?:none|n/a|-)\b", g.strip(), re.I):
            continue
        rel = g.split()[0].strip("`")
        cands = [rel] if os.path.isabs(rel) else [os.path.join(d, rel) for d in (plan_dir, task_dir) if d]
        gp = next((c for c in cands if os.path.isfile(c)), None)
        if gp is None:
            out.append(("FAIL", "step %s: guide %s does not exist" % (st.id, rel)))
            continue
        text = open(gp, encoding="utf-8", errors="replace").read()
        heads = [h.strip().lower() for h in re.findall(r"^##\s+(.+?)\s*$", text, re.M)]
        missing = [h for h in GUIDE_SECTIONS if not any(x.startswith(h) for x in heads)]
        if missing:
            out.append(("FAIL", "step %s: guide lacks section(s): %s" % (st.id, ", ".join(missing))))
        words = len(re.sub(r"<!--.*?-->", "", text, flags=re.S).split())
        if words > GUIDE_WORDS_MAX:
            out.append(("WARN", "step %s: guide is %d words (aim for ≤ ~500)" % (st.id, words)))
        if files is None:
            out.append(("WARN", "step %s: guide paths not checked (no worktree or repo to check against)" % st.id))
            continue
        bad, known_in_approach, section = [], 0, ""
        for line in text.splitlines():
            m = re.match(r"^##\s+(.+?)\s*$", line)
            if m:
                section = m.group(1).strip().lower()
                continue
            if section.startswith("verify"):
                continue
            for tok in BACKTICK_RE.findall(line):
                path = _cited_path(tok)
                if not path:
                    continue
                if _path_known(path, files):
                    known_in_approach += section.startswith("approach")
                elif not NEW_MARK_RE.search(line) and path not in bad:
                    bad.append(path)
        if not known_in_approach:
            out.append(("FAIL", "step %s: guide's Approach cites no existing file in the repo — no pattern to follow"
                        " (route the step to Sonnet: `guide: none`)" % st.id))
        if bad:
            out.append(("WARN", "step %s: guide cites path(s) not in the repo (fine if the step creates them): %s"
                        % (st.id, ", ".join(bad[:5]) + (" …" if len(bad) > 5 else ""))))
    return out


def _summary_index(plan, summary):
    """Count of numbered step lines under the Summary's `- steps:` bullet; None when it has none to count."""
    lines = plan.lines[summary.start + 1:summary.end]
    on, count = False, 0
    for l in lines:
        m = FIELD_LINE_RE.match(l)
        if m:
            on = m.group(1).strip().lower() == "steps"
            if on:
                count += len(re.findall(r"(?:^|[\s;(])\d+[a-z]?\.\s+\S", m.group(2)))
            continue
        if on and SUMMARY_STEP_LINE_RE.match(l):
            count += 1
    return count or None


def is_roadmap(path, plan):
    if os.path.basename(path) == "roadmap.md":
        return True
    return not plan.steps and bool(re.search(r"^\|\s*M\s*\|\s*goal\s*\|", plan.text, re.I | re.M))


def program_check_main(path):
    spec_path = os.path.join(BIN_DIR, "orch-program-check.py")
    if not os.path.exists(spec_path):
        raise OrchError("%s missing: cannot check a roadmap" % spec_path, 2)
    spec = importlib.util.spec_from_file_location("orch_program_check", spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    saved = list(sys.argv)
    sys.argv = [spec_path, path]
    try:
        rc = mod.main()
    finally:
        sys.argv = saved
    return rc if isinstance(rc, int) else 0


def cmd_plan_check(ns, extra):
    from . import cli
    path = ns.path
    if path is None:
        arg = getattr(ns, "task_sub", None) or ns.task
        task = cli.get_task(ns) if arg else None
        if not task:
            raise OrchError("plan-check needs a PATH or -t TASK", 1)
        for name in ("plan.md", "roadmap.md"):
            if os.path.exists(os.path.join(task, name)):
                path = os.path.join(task, name)
                break
        if path is None:
            raise OrchError("no plan.md or roadmap.md in %s" % task, 1)
    try:
        plan = parse_file(path)
    except OrchError as exc:
        print("ERROR %s" % exc)
        return 2
    if is_roadmap(path, plan):
        return program_check_main(path)
    findings = check_plan(plan, ns.lite)
    task_dir, roots = None, []
    if ns.repo:
        roots.append(ns.repo)
    arg = getattr(ns, "task_sub", None) or ns.task
    if arg:
        try:
            task_dir = cli.get_task(ns)
            stt = cli.load_status(task_dir)
            from . import status as st
            for key in ("worktree", "repo"):
                v = st.clean_value(key, stt.get(key)).value
                if v and os.path.isdir(v):
                    roots.append(v)
        except OrchError:
            pass
    if plan.header.get("repo"):
        roots.append(plan.header.get("repo").strip())
    findings += check_guides(plan, os.path.dirname(os.path.abspath(path)), task_dir, roots)
    for level, msg in findings:
        print("%s: %s" % (level, msg))
    fails = sum(1 for l, _ in findings if l == "FAIL")
    warns = len(findings) - fails
    print("plan-check: %s (%d fail, %d warn; %d step(s))" % ("FAIL" if fails else "OK", fails, warns, len(plan.steps)))
    return 1 if fails else 0


def register(add):
    sp = add("plan-check", cmd_plan_check, "check a plan's structure (roadmaps go to orch-program-check.py)")
    sp.add_argument("path", nargs="?", default=None, metavar="PATH")
    sp.add_argument("--lite", action="store_true")
    sp.add_argument("--repo", default=None, help="checkout to resolve guide paths against (default: the task's worktree, then repo)")
