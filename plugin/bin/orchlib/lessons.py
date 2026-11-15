"""Lessons: the old LESSONS.md read for briefs (dated entries, the `- role:` line), and the lesson files.

A scope (the global one, or one repo) with a `lessons/` folder keeps one file per lesson there; its LESSONS.md is then
generated from the files (`orch lessons index`) and a brief carries one index line per open lesson. A scope without the
folder is read the old way: the LESSONS.md in full (repo) or the role/mention-matched entries (global).

Layout: `<orch root>/lessons/` (global) and `<orch root>/<repo>/lessons/`; a file is `<YYYY-MM-DD>-<slug>.md`, its id
`global/<name>` or `<repo>/<name>` (name = the file name without `.md`); `lessons/PROMOTED.md` and `lessons/archive/`
hold what no longer reaches a brief."""
import argparse
import datetime
import json
import os
import re
import shutil
import tempfile

from . import cli
from .paths import OrchError, backups_dir, now, orch_root, require_write_allowed, write_atomic
from .plan import fence_mask

ENTRY_RE = re.compile(r"^##[ \t]+(\d{4}-\d\d-\d\d)\b.*$")
ROLE_LINE_RE = re.compile(r"^- role:[ \t]*(.*)$", re.I)
ROLE_WORD_RE = re.compile(r"[a-z][a-z-]*")


class Entry(object):
    def __init__(self, title, roles, text):
        self.title, self.roles, self.text = title, roles, text


def read_text(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", "surrogateescape")
    except OSError:
        return None


def parse_entries(text):
    """Dated `## YYYY-MM-DD — title` entries (outside code fences) with the role words of their `- role:` line."""
    lines = text.split("\n")
    mask = fence_mask(lines)
    starts = [i for i, l in enumerate(lines) if not mask[i] and ENTRY_RE.match(l)]
    out = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        # an entry ends at the next level-1/2 heading too (the "Open" / "Promoted" sections)
        for j in range(s + 1, e):
            if not mask[j] and re.match(r"^#{1,2}[ \t]+\S", lines[j]):
                e = j
                break
        while e > s and not lines[e - 1].strip():
            e -= 1
        roles = set()
        for l in lines[s + 1:e]:
            m = ROLE_LINE_RE.match(l)
            if m:
                roles.update(ROLE_WORD_RE.findall(m.group(1).lower()))
                break
        out.append(Entry(lines[s].strip(), roles, "\n".join(lines[s:e])))
    return out


def role_words(role):
    """Role words a lesson's `- role:` line may use for this brief's role."""
    base = role.split(":")[0].lower()
    words = {base}
    if base in ("code-reviewer", "security-reviewer", "plan-reviewer"):
        words.add(base.replace("-reviewer", "") + "-reviewer")
    return words


def global_entries(path, role, mention):
    """Global entries that name `role` on their role line, or that `mention(text)` says mention a step file
    (path or basename) or the repo."""
    text = read_text(path)
    if text is None:
        return []
    words = role_words(role)
    return [e for e in parse_entries(text) if (e.roles & words) or mention(e.text)]


def repo_file(path):
    """The repo LESSONS.md in full (stripped of trailing blank lines), or None."""
    text = read_text(path)
    if text is None or not text.strip():
        return None
    return text.rstrip("\n")


def repo_mention_re(repo_name):
    """Word-bounded, case-insensitive match on the repo name."""
    if not repo_name:
        return None
    return re.compile(r"(?<![\w])" + re.escape(repo_name) + r"(?![\w])", re.I)


def lessons_section(task_dir, orch_root_dir, repo_name, role, keys, repo_full=True):
    """Text of `## Lessons`. A scope with a lessons/ folder: one index line per open lesson (a repo's all of them, the
    global scope's those naming the role or mentioning a step file or the repo), with the file's absolute path. A scope
    without the folder: the repo LESSONS.md in full, then the matching global entries."""
    parts = []
    rname = repo_mention_re(repo_name)

    def mention(text):
        return bool((keys.re and keys.re.search(text)) or (rname and rname.search(text)))

    repo_dir = os.path.dirname(task_dir)
    repo_path = os.path.join(repo_dir, "LESSONS.md")
    glob_path = os.path.join(orch_root_dir, "LESSONS.md")
    same = os.path.realpath(glob_path) == os.path.realpath(repo_path)
    open_hint = "open the file when a line applies"
    if repo_full:
        if os.path.isdir(lesson_dir(repo_dir)) and not same:
            lessons = open_lessons(repo_dir, "project")
            if lessons:
                parts.append("### Repo lessons (%s; %s)\n\n%s" % (repo_path, open_hint, index_lines(lessons)))
        else:
            body = repo_file(repo_path)
            if body:
                parts.append("### Repo lessons (%s, in full)\n\n%s" % (repo_path, body))
    if not same:
        if os.path.isdir(lesson_dir(orch_root_dir)):
            words = role_words(role)
            lessons = [l for l in open_lessons(orch_root_dir, "global")
                       if set(ROLE_WORD_RE.findall((l.get("role") or "").lower())) & words or mention(lesson_text(l))]
            if lessons:
                parts.append("### Global lessons that apply (%s; %s)\n\n%s" % (glob_path, open_hint, index_lines(lessons)))
        else:
            entries = global_entries(glob_path, role, mention)
            if entries:
                parts.append("### Global lessons that apply (%s)\n\n%s" % (glob_path, "\n\n".join(e.text for e in entries)))
    return "\n\n".join(parts)


# -- lesson files ----------------------------------------------------------------------------------

STATUSES = ("open", "promoted", "archived")
SCOPES = ("project", "global")
REQUIRED = ("status", "role", "scope", "summary", "quote", "review-by", "exempt", "lesson")
SUMMARY_MAX = 200
NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([a-z0-9-]{1,60})$")
FIELD_RE = re.compile(r"^- ([A-Za-z][A-Za-z -]*?):[ \t]*(.*?)[ \t]*$")
TITLE_RE = re.compile(r"^#[ \t]+(\S.*?)[ \t]*$")
OBSERVED_RE = re.compile(r"^none \(observed: \S.*\)$")
EXEMPT_RE = re.compile(r"^yes\s*[\u2014-]\s*\S")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
GENERATED = "<!-- Generated by `orch lessons index` from lessons/*.md. Edit the files in lessons/, never this file. -->"


class Lesson(object):
    def __init__(self, path, title, fields):
        self.path, self.title, self.fields = path, title, fields     # fields: [(key, value)] in file order

    def get(self, key):
        for k, v in self.fields:
            if k == key:
                return v
        return None

    @property
    def name(self):
        return os.path.basename(self.path)[:-3]

    @property
    def date(self):
        return self.name[:10]


def parse_lesson(path, text):
    """Lesson from a file's text: `# title`, then `- field: value` lines; a line that is not a field continues the
    last field's value."""
    title, fields = None, []
    for line in text.split("\n"):
        if title is None:
            m = TITLE_RE.match(line)
            if m:
                title = m.group(1)
            continue
        m = FIELD_RE.match(line)
        if m:
            fields.append([m.group(1), m.group(2)])
        elif line.strip() and fields:
            fields[-1][1] += "\n" + line.strip()
    return Lesson(path, title, [(k, v) for k, v in fields])


def valid_date(text):
    try:
        datetime.datetime.strptime(text, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def problems(lesson, scope, in_archive=False):
    """One line per defect of a lesson file for the scope it sits in (`project` or `global`)."""
    out = []
    base = os.path.basename(lesson.path)
    m = NAME_RE.match(lesson.name) if base.endswith(".md") else None
    if not m or not valid_date(m.group(1)):
        out.append("file name must be <YYYY-MM-DD>-<slug>.md (slug a-z 0-9 -, at most 60 characters)")
    if not lesson.title:
        out.append("no `# title` line")
    seen = set()
    for k, _ in lesson.fields:
        if k in seen:
            out.append("field %r twice" % k)
        seen.add(k)
    for k in REQUIRED:
        if not (lesson.get(k) or "").strip():
            out.append("missing field %r" % k)
    status = lesson.get("status")
    if status and status not in STATUSES:
        out.append("status must be open, promoted or archived (got %r)" % status)
    elif status:
        if in_archive and status == "open":
            out.append("status open but the file is in archive/")
        if not in_archive and status != "open":
            out.append("status %s but the file is not in archive/" % status)
    sc = lesson.get("scope")
    if sc and sc not in SCOPES:
        out.append("scope must be project or global (got %r)" % sc)
    elif sc and sc != scope:
        out.append("scope %s but the file sits in a %s lessons folder" % (sc, scope))
    summary = lesson.get("summary")
    if summary:
        if "\n" in summary:
            out.append("summary must be one line")
        elif len(summary) > SUMMARY_MAX:
            out.append("summary is %d characters, over %d" % (len(summary), SUMMARY_MAX))
    quote = lesson.get("quote")
    if quote and not ((len(quote) > 2 and quote.startswith('"') and quote.endswith('"')) or OBSERVED_RE.match(quote)):
        out.append('quote must be "<the user\'s words>" or none (observed: <where>)')
    review = lesson.get("review-by")
    if review and not valid_date(review):
        out.append("review-by must be YYYY-MM-DD (got %r)" % review)
    ex = lesson.get("exempt")
    if ex and ex != "no" and not EXEMPT_RE.match(ex):
        out.append("exempt must be `no` or `yes \u2014 <why>`")
    return out


def lesson_dir(scope_root):
    return os.path.join(scope_root, "lessons")


def scope_roots(root):
    """[(label, scope, dir holding LESSONS.md and lessons/)] for every scope under `root` that has a lessons/ folder:
    the global one first, then each repo folder, sorted."""
    out = []
    if os.path.isdir(lesson_dir(root)):
        out.append(("global", "global", root))
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for n in names:
        d = os.path.join(root, n)
        if n != "lessons" and os.path.isdir(lesson_dir(d)):
            out.append((n, "project", d))
    return out


def read_lessons(scope_root, scope, archive=False):
    """[Lesson] of the `.md` files in lessons/ (or lessons/archive/), oldest first. PROMOTED.md is not a lesson."""
    d = lesson_dir(scope_root)
    if archive:
        d = os.path.join(d, "archive")
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".md") and n != "PROMOTED.md" and os.path.isfile(os.path.join(d, n)))
    except OSError:
        return []
    out = []
    for n in names:
        text = read_text(os.path.join(d, n)) or ""
        out.append(parse_lesson(os.path.join(d, n), text))
    return out


def open_lessons(scope_root, scope):
    return [l for l in read_lessons(scope_root, scope) if l.get("status") == "open"]


def roles_of(lesson):
    return " ".join((lesson.get("role") or "").split())


def render_index(scope_root, scope):
    """The generated LESSONS.md of a scope: a notice, one line per open lesson (oldest first), a pointer line."""
    lines = [GENERATED]
    for l in sorted(open_lessons(scope_root, scope), key=lambda x: x.name):
        summary = " ".join((l.get("summary") or "").split())
        lines.append("- %s \u00b7 %s \u00b7 %s \u2192 lessons/%s" % (l.date, roles_of(l), summary, os.path.basename(l.path)))
    pointers = []
    if os.path.isfile(os.path.join(lesson_dir(scope_root), "PROMOTED.md")):
        pointers.append("lessons/PROMOTED.md")
    if os.path.isdir(os.path.join(lesson_dir(scope_root), "archive")):
        pointers.append("lessons/archive/")
    if pointers:
        lines.append("Promoted and archived lessons: %s" % " and ".join(pointers))
    return "\n".join(lines) + "\n"


def write_index(scope_root, scope):
    """Regenerate scope_root/LESSONS.md; True when the file changed."""
    path = os.path.join(scope_root, "LESSONS.md")
    fresh = render_index(scope_root, scope)
    if read_text(path) == fresh:
        return False
    write_atomic(path, fresh.encode("utf-8"), 0o644)
    return True


def check_root(root):
    """(problems, number of scopes, number of lesson files) for every scope under root with a lessons/ folder."""
    out, files, scopes = [], 0, scope_roots(root)
    for label, scope, d in scopes:
        for archive in (False, True):
            for l in read_lessons(d, scope, archive):
                files += 1
                for p in problems(l, scope, archive):
                    out.append("%s/%s%s: %s" % (label, "archive/" if archive else "", os.path.basename(l.path), p))
        path = os.path.join(d, "LESSONS.md")
        have = read_text(path)
        if have is None:
            out.append("%s: LESSONS.md is missing (run `orch lessons index`)" % label)
        elif have != render_index(d, scope):
            out.append("%s: LESSONS.md differs from the files in lessons/ (generated; run `orch lessons index`)" % label)
    return out, len(scopes), files


# -- a brief's lessons section ---------------------------------------------------------------------

def index_lines(lessons):
    return "\n".join("- %s \u00b7 %s \u00b7 %s \u2192 %s" % (l.date, roles_of(l), " ".join((l.get("summary") or "").split()), l.path)
                     for l in sorted(lessons, key=lambda x: x.name))


def lesson_text(l):
    return "\n".join(["# " + (l.title or "")] + ["- %s: %s" % (k, v) for k, v in l.fields])


# -- orch lessons add | promote | index | check -----------------------------------------------------

def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60].strip("-")


def scope_dir_for(ns, scope):
    root = orch_root()
    if scope == "global":
        return root
    repo = ns.repo
    if not repo:
        try:
            task_dir = cli.get_task(ns)
            repo = os.path.basename(os.path.dirname(task_dir))
        except OrchError:
            raise OrchError("--scope project needs --repo NAME (or -t <repo>/<slug>)", 1)
    if not REPO_RE.match(repo) or not os.path.isdir(os.path.join(root, repo)):
        raise OrchError("no repo folder %r under %s" % (repo, root), 1)
    return os.path.join(root, repo)


def one(text, flag):
    v = cli.one_line(text or "")
    if not v:
        raise OrchError("%s is required" % flag, 1)
    return v


def cmd_add(ns):
    scope = ns.scope
    if scope == "task":
        raise OrchError("a one-task directive belongs in that task's plan, not in a lesson (scope is project or global)", 1)
    if scope not in SCOPES:
        raise OrchError("--scope must be project or global", 1)
    title = one(ns.title, "--title")
    role = one(ns.role, "--role")
    lesson = one(ns.lesson, "--lesson")
    if ns.summary is not None and ("\n" in ns.summary or "\r" in ns.summary):
        raise OrchError("the summary must be one line", 1)
    summary = one(ns.summary, "--summary")
    if len(summary) > SUMMARY_MAX:
        raise OrchError("the summary is %d characters, over %d: say the instruction in one short line" % (len(summary), SUMMARY_MAX), 1)
    quote = one(ns.quote, "--quote")
    review = one(ns.review_by, "--review-by")
    if not valid_date(review):
        raise OrchError("--review-by must be YYYY-MM-DD", 1)
    exempt = "no" if not ns.exempt else "yes \u2014 " + one(ns.exempt, "--exempt")
    if not ROLE_WORD_RE.findall(role.lower()):
        raise OrchError("--role must name a role (implementer, planner, ...)", 1)
    slug = slugify(title)
    if not slug:
        raise OrchError("--title gives an empty file name", 1)
    require_write_allowed()
    d = scope_dir_for(ns, scope)
    name = "%s-%s" % (now()[:10], slug)
    path = os.path.join(lesson_dir(d), name + ".md")
    fields = [("status", "open"), ("role", role), ("scope", scope)]
    if ns.task_note:
        fields.append(("task", one(ns.task_note, "--task")))
    fields += [("summary", summary), ("quote", quote), ("review-by", review), ("exempt", exempt)]
    if ns.what_happened:
        fields.append(("what happened", one(ns.what_happened, "--what-happened")))
    if ns.actually_true:
        fields.append(("what was actually true", one(ns.actually_true, "--actually-true")))
    fields.append(("lesson", lesson))
    new = Lesson(path, title, fields)
    bad = problems(new, scope)
    if bad:
        raise OrchError("; ".join(bad), 1)
    for sub in (lesson_dir(d), os.path.join(lesson_dir(d), "archive")):
        if os.path.exists(os.path.join(sub, name + ".md")):
            raise OrchError("lesson %s already exists" % name, 1)
    os.makedirs(lesson_dir(d), exist_ok=True)
    write_atomic(path, (lesson_text(new) + "\n").encode("utf-8"), 0o644)
    write_index(d, scope)
    print("%s/%s" % ("global" if scope == "global" else os.path.basename(d), name))
    return 0


def find_lesson(lesson_id):
    """(scope_root, scope, Lesson) of an open lesson id `global/<name>` or `<repo>/<name>`."""
    label, _, name = (lesson_id or "").partition("/")
    if not label or not name or "/" in name or not NAME_RE.match(name):
        raise OrchError("lesson id is global/<name> or <repo>/<name> (<name> = <YYYY-MM-DD>-<slug>)", 1)
    root = orch_root()
    if label == "global":
        d, scope = root, "global"
    else:
        if not REPO_RE.match(label):
            raise OrchError("bad lesson id %r" % lesson_id, 1)
        d, scope = os.path.join(root, label), "project"
    path = os.path.join(lesson_dir(d), name + ".md")
    if not os.path.isfile(path):
        raise OrchError("no open lesson %s (looked for %s)" % (lesson_id, path), 1)
    return d, scope, parse_lesson(path, read_text(path) or "")


def set_status_text(text, status):
    out, done = [], False
    for line in text.split("\n"):
        m = FIELD_RE.match(line)
        if m and m.group(1) == "status" and not done:
            line, done = "- status: %s" % status, True
        out.append(line)
    if not done:
        raise OrchError("the lesson has no status line", 1)
    return "\n".join(out)


def cmd_promote(ns):
    if not ns.arg:
        raise OrchError("promote takes a lesson id", 1)
    where = one(ns.to, "--to")
    require_write_allowed()
    d, scope, l = find_lesson(ns.arg)
    if l.get("status") != "open":
        raise OrchError("%s is %s, not open" % (ns.arg, l.get("status")), 1)
    ldir = lesson_dir(d)
    archive = os.path.join(ldir, "archive")
    os.makedirs(archive, exist_ok=True)
    dest = os.path.join(archive, os.path.basename(l.path))
    if os.path.exists(dest):
        raise OrchError("%s already exists" % dest, 1)
    text = read_text(l.path)
    write_atomic(dest, set_status_text(text, "promoted").encode("utf-8"), 0o644)
    table = os.path.join(ldir, "PROMOTED.md")
    have = read_text(table)
    if have is None:
        have = "# Promoted lessons\n\n| date | lesson | promoted to |\n|---|---|---|\n"
    cell = lambda t: " ".join(t.split()).replace("|", "/")
    row = "| %s | %s (%s) | %s |\n" % (now()[:10], cell(l.get("summary") or l.title or ""), l.name, cell(where))
    write_atomic(table, (have.rstrip("\n") + "\n" + row).encode("utf-8"), 0o644)
    os.unlink(l.path)
    write_index(d, scope)
    print("promoted %s -> %s" % (ns.arg, where))
    return 0


def cmd_index(ns):
    require_write_allowed()
    root = orch_root()
    done = 0
    for label, scope, d in scope_roots(root):
        if ns.scope and ns.scope != scope:
            continue
        if ns.repo and ns.repo != label:
            continue
        changed = write_index(d, scope)
        done += 1
        print("%s: LESSONS.md %s" % (label, "written" if changed else "unchanged"))
    if not done:
        print("no scope with a lessons/ folder (nothing to index)")
    return 0


def cmd_check(ns):
    root = ns.root or orch_root()
    out, scopes, files = check_root(root)
    for line in out:
        print(line)
    if out:
        print("lessons: %d problem%s in %d file%s" % (len(out), "" if len(out) == 1 else "s", files, "" if files == 1 else "s"))
        return 1
    if not scopes:
        print("lessons: no lesson files (skip)")
    else:
        print("lessons: %d file%s in %d scope%s, clean" % (files, "" if files == 1 else "s", scopes, "" if scopes == 1 else "s"))
    return 0


# -- orch lessons migrate: legacy LESSONS.md -> lesson files ----------------------------------------
#
# A legacy scope is a folder holding LESSONS.md and no lessons/ folder. Its open entries are the dated `## YYYY-MM-DD`
# entries outside a closed section (any level-2 heading other than `## Open`, such as `## Promoted`). Each becomes a
# lesson file: `# <title>`, the fields migration adds, then the entry's own lines verbatim. Everything else in the file
# (header prose, the Promoted table) goes to lessons/PROMOTED.md unchanged.

REVIEW_DAYS = 90
HEADING_RE = re.compile(r"^#{1,2}[ \t]+\S")
DATED_RE = re.compile(r"^##[ \t]+(\d{4}-\d\d-\d\d)[ \t]*(?:[—–:-]+[ \t]*)?(.*?)[ \t]*$")
OPEN_SECTION_RE = re.compile(r"^##[ \t]+open\b", re.I)


class Planned(object):
    """One open legacy entry and the lesson file it becomes."""

    def __init__(self, label, name, title, body):
        self.label, self.name, self.title, self.body = label, name, title, body
        self.fields = parse_lesson("", "# t\n" + "\n".join(body)).fields

    @property
    def id(self):
        return "%s/%s" % (self.label, self.name)

    def get(self, key):
        for k, v in self.fields:
            if k == key:
                return v
        return None

    @property
    def lesson(self):
        return self.get("lesson") or ""

    @property
    def needs_summary(self):
        return len(self.lesson) > SUMMARY_MAX or "\n" in self.lesson

    def proposed(self):
        return None if self.needs_summary else self.lesson

    def render(self, scope, summary, exempt, today, review_by):
        added = [("status", "open"), ("scope", scope), ("summary", summary),
                 ("quote", "none (observed: migrated %s)" % today), ("review-by", review_by), ("exempt", exempt)]
        lines = ["# " + self.title] + ["- %s: %s" % kv for kv in added] + list(self.body)
        return "\n".join(lines) + "\n"


class LegacyScope(object):
    def __init__(self, label, scope, d, text):
        self.label, self.scope, self.dir, self.text = label, scope, d, text
        self.entries, self.rest = split_legacy(label, text)

    @property
    def path(self):
        return os.path.join(self.dir, "LESSONS.md")


def split_legacy(label, text):
    """([Planned] open entries in file order, the rest of the text with those entries cut out)."""
    lines = text.split("\n")
    mask = fence_mask(lines)
    heads = [i for i, l in enumerate(lines) if not mask[i] and HEADING_RE.match(l)]
    cut, entries, used, section_open = set(), [], set(), True
    for k, h in enumerate(heads):
        nxt = heads[k + 1] if k + 1 < len(heads) else len(lines)
        m = DATED_RE.match(lines[h])
        if not m:
            section_open = lines[h].startswith("# ") or bool(OPEN_SECTION_RE.match(lines[h]))
            continue
        if not section_open:
            continue
        body = lines[h + 1:nxt]
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        title = m.group(2) or "lesson"
        slug = slugify(title) or "lesson"
        name, n = "%s-%s" % (m.group(1), slug), 1
        while name in used:
            n += 1
            suffix = "-%d" % n
            name = "%s-%s%s" % (m.group(1), slug[:60 - len(suffix)].strip("-"), suffix)
        used.add(name)
        entries.append(Planned(label, name, title, body))
        cut.update(range(h, nxt))
    rest = "\n".join(l for i, l in enumerate(lines) if i not in cut).rstrip("\n")
    return entries, (rest + "\n") if rest.strip() else ""


def legacy_scopes(root):
    """([LegacyScope] global first then repos sorted, [labels of scopes already migrated])."""
    out, done = [], []
    cands = [("global", "global", root)]
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    cands += [(n, "project", os.path.join(root, n)) for n in names
              if n != "lessons" and os.path.isdir(os.path.join(root, n))]
    for label, scope, d in cands:
        path = os.path.join(d, "LESSONS.md")
        if os.path.isdir(lesson_dir(d)):
            done.append(label)
            continue
        if not os.path.isfile(path):
            continue
        text = read_text(path)
        if text is not None:
            out.append(LegacyScope(label, scope, d, text))
    return out, done


def review_by_date(today):
    return (datetime.datetime.strptime(today, "%Y-%m-%d") + datetime.timedelta(days=REVIEW_DAYS)).strftime("%Y-%m-%d")


def entry_problems(scope, e, summary, exempt, today):
    """Defects the migrated file would have (a legacy entry without a role line, a clashing field name)."""
    text = e.render(scope.scope, summary, exempt, today, review_by_date(today))
    return problems(parse_lesson(os.path.join(lesson_dir(scope.dir), e.name + ".md"), text), scope.scope)


def migrate_dry_run(root, as_json):
    scopes, done = legacy_scopes(root)
    today = now()[:10]
    report = {"root": root, "scopes": [], "already_migrated": done, "entries": 0, "need_summary": 0}
    for s in scopes:
        rows = []
        for e in s.entries:
            summ = e.proposed()
            rows.append({"id": e.id, "title": e.title, "roles": e.get("role") or "", "length": len(e.lesson),
                         "needs_summary": e.needs_summary, "summary": summ,
                         "problems": entry_problems(s, e, summ or "x", "no", today)})
        note = None
        if not s.entries:
            note = ("no open entry: the whole file moves to lessons/PROMOTED.md and stops reaching briefs; "
                    "LESSONS.md becomes an empty index")
        report["scopes"].append({"label": s.label, "path": s.path, "entries": rows, "note": note,
                                 "moves_to_promoted": len(s.rest.splitlines())})
        report["entries"] += len(rows)
        report["need_summary"] += sum(1 for r in rows if r["needs_summary"])
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    for sc in report["scopes"]:
        print("%s: %d open entr%s (%s)%s" % (sc["label"], len(sc["entries"]), "y" if len(sc["entries"]) == 1 else "ies",
                                            sc["path"], "; " + sc["note"] if sc["note"] else ""))
        for r in sc["entries"]:
            print("  %s" % r["id"])
            print("    title: %s" % r["title"])
            print("    roles: %s" % (r["roles"] or "(none)"))
            print("    length: %d  needs_summary: %s" % (r["length"], "yes" if r["needs_summary"] else "no"))
            print("    summary: %s" % (r["summary"] if r["summary"] is not None else "(needs one)"))
            for p in r["problems"]:
                print("    problem: %s" % p)
    for label in done:
        print("%s: has lessons/ already (skipped)" % label)
    print("%d open entr%s in %d legacy scope%s; %d need a summary; nothing written" % (
        report["entries"], "y" if report["entries"] == 1 else "ies", len(scopes), "" if len(scopes) == 1 else "s",
        report["need_summary"]))
    return 0


def load_summaries(path):
    try:
        with open(path, "rb") as fh:
            data = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise OrchError("cannot read --summaries %s: %s" % (path, exc.__class__.__name__), 1)
    if not isinstance(data, dict):
        raise OrchError("--summaries must be a JSON object: id -> {summary, exempt}", 1)
    out, bad = {}, []
    for k, v in data.items():
        if not isinstance(v, dict) or set(v) - {"summary", "exempt"} or \
                not all(isinstance(v.get(f), str) for f in ("summary", "exempt") if v.get(f) is not None):
            bad.append("%s: the value must be {\"summary\": \"...\", \"exempt\": \"no\" | \"yes — <why>\"}" % k)
            continue
        out[k] = v
    if bad:
        raise OrchError("\n".join(bad), 1)
    return out


def migrate_plan(root, summaries):
    """(scopes, {id: (summary, exempt)}) or OrchError listing every reason the apply is refused."""
    scopes, done = legacy_scopes(root)
    today = now()[:10]
    known = set()
    for label, scope, d in scope_roots(root):
        for archive in (False, True):
            known.update("%s/%s" % (label, l.name) for l in read_lessons(d, scope, archive))
    errors, chosen = [], {}
    planned = {e.id for s in scopes for e in s.entries}
    for k in sorted(summaries):
        if k not in planned and k not in known:
            errors.append("%s: no such entry" % k)
    for s in scopes:
        for e in s.entries:
            given = summaries.get(e.id) or {}
            summary = given.get("summary")
            if summary is None:
                summary = e.proposed()
            exempt = given.get("exempt") or "no"
            if summary is None:
                errors.append("%s: needs a summary (the lesson is %d characters%s)" % (
                    e.id, len(e.lesson), ", more than one line" if "\n" in e.lesson else ""))
                continue
            if "\n" in summary or len(summary) > SUMMARY_MAX:
                errors.append("%s: the summary must be one line of at most %d characters (got %d)" % (
                    e.id, SUMMARY_MAX, len(summary)))
                continue
            for p in entry_problems(s, e, summary, exempt, today):
                errors.append("%s: %s" % (e.id, p))
            chosen[e.id] = (summary, exempt)
    if errors:
        raise OrchError("migrate refused, nothing written:\n" + "\n".join("  " + x for x in errors), 1)
    return scopes, chosen


def unused_path(path):
    out, n = path, 1
    while os.path.exists(out):
        n += 1
        out = "%s-%d" % (path, n)
    return out


def write_scope(s, chosen, today, bdir):
    """Write one scope all or nothing: stage lessons/ beside it, rename it in, regenerate LESSONS.md."""
    target = lesson_dir(s.dir)
    stage = tempfile.mkdtemp(prefix=".lessons-migrate-", dir=s.dir)
    try:
        os.chmod(stage, 0o755)
        for e in s.entries:
            summary, exempt = chosen[e.id]
            write_atomic(os.path.join(stage, e.name + ".md"),
                         e.render(s.scope, summary, exempt, today, review_by_date(today)).encode("utf-8"), 0o644)
        if s.rest:
            write_atomic(os.path.join(stage, "PROMOTED.md"), s.rest.encode("utf-8", "surrogateescape"), 0o644)
        written = [n for n in os.listdir(stage) if n.endswith(".md") and n != "PROMOTED.md"]
        if len(written) != len(s.entries):          # no entry is dropped
            raise OrchError("%s: wrote %d lesson files for %d entries" % (s.label, len(written), len(s.entries)), 3)
        if os.path.exists(target):
            raise OrchError("%s: %s appeared during the migration" % (s.label, target), 3)
        os.rename(stage, target)
    except BaseException:
        if os.path.isdir(stage):
            shutil.rmtree(stage, ignore_errors=True)      # the staging dir is this run's own
        raise
    try:
        write_index(s.dir, s.scope)
        if len(open_lessons(s.dir, s.scope)) != len(s.entries):
            raise OrchError("%s: the lessons folder holds a different number of open lessons than the entries" % s.label, 3)
    except BaseException:
        shutil.move(target, unused_path(os.path.join(bdir, s.label, "lessons-failed")))
        write_atomic(s.path, s.text.encode("utf-8", "surrogateescape"))
        raise


def migrate_apply(root, summaries_path, dry):
    scopes, chosen = migrate_plan(root, load_summaries(summaries_path))
    count = sum(len(s.entries) for s in scopes)
    if dry:
        print("apply would write %d lesson file%s in %d scope%s; nothing written" % (
            count, "" if count == 1 else "s", len(scopes), "" if len(scopes) == 1 else "s"))
        return 0
    require_write_allowed()
    today = now()[:10]
    stamp = os.path.join(lesson_dir(root), ".tracking")
    if not scopes:
        print("nothing to migrate (no legacy LESSONS.md under %s)" % root)
        return 0
    created = []
    if not any(s.label == "global" for s in scopes):
        if not os.path.isdir(lesson_dir(root)):
            created.append("lessons")
        elif not os.path.exists(stamp):
            created.append("lessons/.tracking")
        if not os.path.exists(os.path.join(root, "LESSONS.md")):
            created.append("LESSONS.md")
    bdir = unused_path(os.path.join(backups_dir(), "lessons-" + now().replace("-", "").replace(":", "")))
    for s in scopes:
        dest = os.path.join(bdir, s.label, "LESSONS.md")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(s.path, "rb") as fh:
            orig = fh.read()
        write_atomic(dest, orig, 0o644)
        with open(dest, "rb") as fh:
            if fh.read() != orig:
                raise OrchError("backup of %s does not match the original; nothing migrated" % s.path, 3)
    manifest = {"root": os.path.realpath(root), "scopes": {s.label: os.path.relpath(s.dir, root) for s in scopes},
                "created": created}
    write_atomic(os.path.join(bdir, "manifest.json"), (json.dumps(manifest, indent=2) + "\n").encode("utf-8"), 0o644)
    written = 0
    for s in scopes:
        write_scope(s, chosen, today, bdir)
        written += len(s.entries)
        print("%s: %d lesson file%s, LESSONS.md regenerated%s" % (
            s.label, len(s.entries), "" if len(s.entries) == 1 else "s", ", PROMOTED.md" if s.rest else ""))
    assert written == count, (written, count)      # no entry is dropped: the files written equal the dry-run's count
    if not os.path.exists(stamp):
        os.makedirs(lesson_dir(root), exist_ok=True)
        write_atomic(stamp, (json.dumps({"since": today}) + "\n").encode("utf-8"), 0o644)
    if not os.path.exists(os.path.join(root, "LESSONS.md")):
        write_index(root, "global")
    print("migrated %d entr%s in %d scope%s; backup %s" % (written, "y" if written == 1 else "ies", len(scopes),
                                                          "" if len(scopes) == 1 else "s", bdir))
    out, nscopes, files = check_root(root)
    for line in out:
        print(line)
    print("lessons check: %s" % ("%d problem%s" % (len(out), "" if len(out) == 1 else "s") if out else
                                 "%d file%s in %d scope%s, clean" % (files, "" if files == 1 else "s", nscopes,
                                                                    "" if nscopes == 1 else "s")))
    print("undo: orch lessons migrate --restore %s" % bdir)
    return 1 if out else 0


def migrate_restore(bdir):
    require_write_allowed()
    root = orch_root()
    bdir = os.path.abspath(bdir)
    try:
        with open(os.path.join(bdir, "manifest.json"), "rb") as fh:
            manifest = json.loads(fh.read().decode("utf-8"))
        scopes, created = dict(manifest["scopes"]), list(manifest.get("created") or [])
    except (OSError, ValueError, KeyError, TypeError):
        raise OrchError("%s is not a lessons migration backup (no readable manifest.json)" % bdir, 1)
    if manifest.get("root") != os.path.realpath(root):
        raise OrchError("this backup was made for %s, not %s" % (manifest.get("root"), root), 1)
    plan = []
    for label, rel in sorted(scopes.items()):
        d = os.path.normpath(os.path.join(root, rel))
        if os.path.realpath(d) != os.path.realpath(root) and os.path.dirname(os.path.realpath(d)) != os.path.realpath(root):
            raise OrchError("manifest scope %r is outside %s" % (label, root), 1)
        src = os.path.join(bdir, label, "LESSONS.md")
        if not os.path.isfile(src):
            raise OrchError("the backup has no %s" % src, 1)
        plan.append((label, d, src))
    for p in created:
        if p not in ("lessons", "lessons/.tracking", "LESSONS.md"):
            raise OrchError("manifest names an unexpected created path %r" % p, 1)
    for label, d, src in plan:
        if os.path.isdir(lesson_dir(d)):
            shutil.move(lesson_dir(d), unused_path(os.path.join(bdir, label, "lessons")))
        with open(src, "rb") as fh:
            write_atomic(os.path.join(d, "LESSONS.md"), fh.read())
        print("%s: LESSONS.md restored" % label)
    for p in created:
        full = os.path.join(root, p)
        if os.path.exists(full):
            dest = unused_path(os.path.join(bdir, "_created", p))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(full, dest)
    print("restored %d scope%s; the lesson folders are kept in %s" % (len(plan), "" if len(plan) == 1 else "s", bdir))
    return 0


def cmd_migrate(ns):
    if ns.restore:
        if ns.apply or ns.dry_run or ns.summaries or ns.json:
            raise OrchError("--restore takes no other migrate flag", 1)
        return migrate_restore(ns.restore)
    if ns.apply:
        if not ns.summaries:
            raise OrchError("--apply needs --summaries FILE (JSON: id -> {summary, exempt})", 1)
        return migrate_apply(orch_root(), ns.summaries, ns.dry_run)
    if not ns.dry_run:
        raise OrchError("migrate takes --dry-run [--json], --apply --summaries FILE [--dry-run], or --restore DIR", 1)
    if ns.summaries:
        raise OrchError("--summaries goes with --apply", 1)
    return migrate_dry_run(orch_root(), ns.json)


# -- orch cite | orch lessons usage ------------------------------------------------------------------
#
# Use is a citation: a role that let a lesson line change what it did runs `orch cite`, which writes a `cite` journal line
# in the task. A guard record that allowed a `basis: lesson | <id>` spawn counts too. A brief that only loads the lesson,
# and a Read of its file, are reported beside the count and are never use.

TRACKING = ".tracking"
GUARD_LESSON_RE = re.compile(r"\blesson[ \t]*\|[ \t]*(\S+)")
USAGE_NOTE = '"loaded" (briefs that carry the lesson\'s path) and "reads" (Read calls on its file) are not use; only citations are.'


def cmd_cite(ns, extra):
    from . import flow
    from . import journal as jr
    from .paths import TaskLock, task_names
    require_write_allowed()
    why = one(ns.for_text, "--for")
    role = None
    if ns.as_role is not None:
        role = one(ns.as_role, "--as")
        if not flow.ROLE_RE.match(role):
            raise OrchError("bad role %r" % role, 1)
    find_lesson(ns.lesson)
    task_dir = cli.get_task(ns, infer=True)
    ts = now()
    repo, slug = task_names(task_dir)
    schema = jr.load_schema()
    with TaskLock(task_dir):
        program, auto_ms = flow.program_of(flow.status_or_none(task_dir))
        pairs = [("ref", "=", ns.lesson), ("for", "=", why), ("role", ":=", json.dumps(role))]
        rec, _ = jr.build_record(schema, "cite", pairs, ts, repo, slug, program, auto_ms)
        jr.append(os.path.join(task_dir, "journal.jsonl"), jr.encode(rec))
    print("cited %s%s" % (ns.lesson, cli.tag(ns)))
    return 0


def read_json_lines(path):
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        pass
    return out


def tracking_since(root):
    text = read_text(os.path.join(lesson_dir(root), TRACKING))
    if text is None:
        return None
    try:
        since = json.loads(text).get("since")
    except (ValueError, AttributeError):
        return None
    return since if isinstance(since, str) and valid_date(since) else None


def task_dirs(root):
    """[(repo, slug, dir)] for every <root>/<repo>/<slug> folder."""
    out = []
    try:
        repos = sorted(os.listdir(root))
    except OSError:
        return out
    for r in repos:
        rd = os.path.join(root, r)
        if r == "lessons" or not os.path.isdir(rd):
            continue
        try:
            slugs = sorted(os.listdir(rd))
        except OSError:
            continue
        out += [(r, s, os.path.join(rd, s)) for s in slugs if os.path.isdir(os.path.join(rd, s)) and s != "lessons"]
    return out


def citations(root, logs):
    """{lesson id: [(date, task label)]} from the `cite` journal lines of every task and the allowed guard records that
    carry a `lesson` basis (task label `guard`)."""
    found = {}
    for repo, slug, d in task_dirs(root):
        for rec in read_json_lines(os.path.join(d, "journal.jsonl")):
            if rec.get("kind") == "cite" and isinstance(rec.get("ref"), str):
                found.setdefault(rec["ref"], []).append((str(rec.get("ts") or "")[:10], "%s/%s" % (repo, slug)))
    for rec in read_json_lines(os.path.join(logs, "tier-guard.jsonl")):
        if rec.get("decision") != "allow" or rec.get("basis_kind") != "lesson":
            continue
        m = GUARD_LESSON_RE.search(str(rec.get("basis") or ""))
        if m:
            found.setdefault(m.group(1), []).append((str(rec.get("ts") or "")[:10], "guard"))
    return found


def briefs_text(root):
    """[text] of every task's briefs/*.md."""
    out = []
    for _, _, d in task_dirs(root):
        bd = os.path.join(d, "briefs")
        try:
            names = sorted(n for n in os.listdir(bd) if n.endswith(".md"))
        except OSError:
            continue
        for n in names:
            t = read_text(os.path.join(bd, n))
            if t:
                out.append(t)
    return out


def count_reads(paths, since):
    """{path: number of Read tool calls on it} over the transcripts changed on or after `since` (YYYY-MM-DD)."""
    from . import usage
    try:
        floor = datetime.datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        floor = 0
    counts = dict((p, 0) for p in paths)
    for tp in usage.transcripts(usage.home_dir()):
        try:
            if os.path.getmtime(tp) < floor:
                continue
            fh = open(tp, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"Read"' not in line or not any(p in line for p in counts):
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Read":
                        fp = (b.get("input") or {}).get("file_path") if isinstance(b.get("input"), dict) else None
                        if fp in counts:
                            counts[fp] += 1
    return counts


def usage_report(root, logs, with_reads):
    since = tracking_since(root)
    lessons = []
    for label, scope, d in scope_roots(root):
        for l in open_lessons(d, scope):
            lessons.append(("%s/%s" % (label, l.name), l))
    cites = citations(root, logs)
    texts = briefs_text(root)
    reads = count_reads([l.path for _, l in lessons], since) if with_reads and lessons else {}
    rows = []
    for lid, l in lessons:
        c = sorted(cites.get(lid, []))
        row = {"id": lid, "citations": {"count": len(c), "last": c[-1][0] if c else None,
                                         "tasks": sorted(set(t for _, t in c))},
               "loaded": sum(1 for t in texts if l.path in t)}
        if with_reads:
            row["reads"] = reads.get(l.path, 0)
        rows.append(row)
    return {"tracking_since": since, "note": USAGE_NOTE, "lessons": rows}


def cmd_usage(ns):
    from .paths import logs_dir
    report = usage_report(orch_root(), logs_dir(), ns.reads)
    if ns.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print("tracking since: %s" % (report["tracking_since"] or "not started (orch lessons migrate writes lessons/.tracking)"))
    print("note: %s" % report["note"])
    for r in report["lessons"]:
        c = r["citations"]
        line = "%s  citations: %d%s  loaded: %d" % (
            r["id"], c["count"], " (last %s; tasks: %s)" % (c["last"], ", ".join(c["tasks"])) if c["count"] else "", r["loaded"])
        if "reads" in r:
            line += "  reads: %d" % r["reads"]
        print(line)
    if not report["lessons"]:
        print("no open lesson files")
    return 0


# -- orch lessons audit | archive | restore ---------------------------------------------------------
#
# Audit reads and reports; it never writes. Archive and restore move one file between lessons/ and lessons/archive/;
# nothing is ever deleted.

UNUSED_DAYS = 60
BACKTICK_RE = re.compile(r"`([^`\n]+)`")
REL_PATH_RE = re.compile(r"^(?:\.\.?/)*[\w.@+-]+(?:/[\w.@+-]+)+/?$")
ABS_PATH_RE = re.compile(r"^~?(?:/[\w.@+-]+)+/?$")
VERB_RE = re.compile(r"^orch[ \t]+([a-z][a-z-]*)\b")
TRAIL = ".,;:)"


def repo_checkout(scope_dir):
    """(path, why) of the checkout of a repo scope, from the `- repo:` line of a task's status.md under it."""
    try:
        names = sorted(os.listdir(scope_dir))
    except OSError:
        return None, "cannot read %s" % scope_dir
    seen = False
    for n in names:
        sp = os.path.join(scope_dir, n, "status.md")
        text = read_text(sp)
        if text is None:
            continue
        seen = True
        m = re.search(r"^- repo:[ \t]*(\S.*?)[ \t]*$", text, re.M)
        if m:
            path = os.path.expanduser(m.group(1))
            if os.path.isdir(path):
                return path, None
            return None, "the repo path %s in %s/status.md is not a folder" % (m.group(1), n)
    return None, "no task status.md with a repo: line" if seen else "no task under the scope"


def cli_verbs():
    cli.build_parser()
    return set(cli.HANDLERS)


def stale_names(text, repo, verbs):
    """[(token, why)] for the backticked tokens of `text` that name something that does not exist. A repo-relative path
    is checked only when `repo` (the checkout) is known; a token with <>*$ or { } is a pattern and is skipped."""
    out, seen = [], set()
    for raw in BACKTICK_RE.findall(text):
        tok = raw.strip()
        if tok in seen:
            continue
        seen.add(tok)
        m = VERB_RE.match(tok)
        if m:
            if m.group(1) not in verbs:
                out.append((tok, "no `orch %s` command" % m.group(1)))
            continue
        word = tok.split()[0].rstrip(TRAIL) if tok.split() else ""
        if not word or re.search(r"[<>*$?{}|\\]", word):
            continue
        if ABS_PATH_RE.match(word):
            if not os.path.exists(os.path.expanduser(word)):
                out.append((word, "does not exist"))
        elif REL_PATH_RE.match(word) and repo:
            # `and/or` is not a path: a relative token counts when it ends in "/", its last segment has an extension,
            # or its first segment exists in the checkout
            segs = word.rstrip("/").split("/")
            first = next((x for x in segs if x not in (".", "..")), "")
            looks = word.endswith("/") or "." in segs[-1] or os.path.exists(os.path.join(repo, *segs[:segs.index(first) + 1]))
            if looks and not os.path.exists(os.path.join(repo, word)):
                out.append((word, "not in the repo checkout"))
    return out


def audit_report(root, logs, today):
    since = tracking_since(root)
    verbs = cli_verbs()
    if since is None:
        disuse = {"state": "not started", "from": None}
    else:
        start = (datetime.datetime.strptime(since, "%Y-%m-%d") + datetime.timedelta(days=UNUSED_DAYS)).strftime("%Y-%m-%d")
        disuse = {"state": "active" if today >= start else "waits", "from": start}
    cites = citations(root, logs)
    scopes, rows = [], []
    for label, scope, d in scope_roots(root):
        repo, why = (None, None)
        if scope == "project":
            repo, why = repo_checkout(d)
        scopes.append({"label": label, "scope": scope, "repo": repo,
                       "repo_paths_checked": scope == "global" or repo is not None,
                       "note": None if scope == "global" or repo else "not checked: no repo checkout (%s)" % why})
        for l in read_lessons(d, scope):
            lid = "%s/%s" % (label, l.name)
            flags = [{"kind": "invalid", "detail": p} for p in problems(l, scope)]
            if l.get("status") == "open":
                for tok, w in stale_names(" ".join(filter(None, [l.get("summary"), l.get("lesson")])), repo, verbs):
                    flags.append({"kind": "stale-name", "detail": "`%s` %s" % (tok, w)})
                review = l.get("review-by")
                if review and valid_date(review) and review < today:
                    flags.append({"kind": "review-due", "detail": "review-by %s is past" % review})
                if disuse["state"] == "active" and (l.get("exempt") or "").strip() == "no" \
                        and not any(c[0] >= since for c in cites.get(lid, [])):
                    flags.append({"kind": "unused", "detail": "no citation since %s" % since})
            rows.append({"id": lid, "scope": scope, "date": l.date, "summary": " ".join((l.get("summary") or "").split()),
                         "flags": flags})
    return {"today": today, "tracking_since": since, "disuse_checks": disuse, "scopes": scopes, "lessons": rows}


def cmd_audit(ns):
    from .paths import logs_dir
    report = audit_report(orch_root(), logs_dir(), now()[:10])
    if ns.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    dz = report["disuse_checks"]
    print("audit of %d open and other lesson file%s, %s" % (len(report["lessons"]), "" if len(report["lessons"]) == 1 else "s", report["today"]))
    if dz["state"] == "not started":
        print("disuse checks: not started (no tracking date; orch lessons migrate writes it)")
    elif dz["state"] == "waits":
        print("disuse checks: start %s (tracking since %s, %d days)" % (dz["from"], report["tracking_since"], UNUSED_DAYS))
    else:
        print("disuse checks: active (tracking since %s)" % report["tracking_since"])
    for sc in report["scopes"]:
        if sc["note"]:
            print("%s: %s" % (sc["label"], sc["note"]))
    flagged = [r for r in report["lessons"] if r["flags"]]
    for r in flagged:
        print("%s  [%s]  %s" % (r["id"], r["scope"], r["summary"]))
        for f in r["flags"]:
            print("    %s: %s" % (f["kind"], f["detail"]))
    print("%d flagged, %d clean" % (len(flagged), len(report["lessons"]) - len(flagged)))
    return 0


def find_archived(lesson_id):
    """(scope_root, scope, Lesson) of the file lessons/archive/<name>.md for an id."""
    label, _, name = (lesson_id or "").partition("/")
    if not label or not name or "/" in name or not NAME_RE.match(name):
        raise OrchError("lesson id is global/<name> or <repo>/<name> (<name> = <YYYY-MM-DD>-<slug>)", 1)
    root = orch_root()
    if label == "global":
        d, scope = root, "global"
    else:
        if not REPO_RE.match(label):
            raise OrchError("bad lesson id %r" % lesson_id, 1)
        d, scope = os.path.join(root, label), "project"
    path = os.path.join(lesson_dir(d), "archive", name + ".md")
    if not os.path.isfile(path):
        raise OrchError("no archived lesson %s (looked for %s)" % (lesson_id, path), 1)
    return d, scope, parse_lesson(path, read_text(path) or "")


def cmd_archive(ns):
    if not ns.arg:
        raise OrchError("archive takes a lesson id", 1)
    why = one(ns.reason, "--reason")
    require_write_allowed()
    d, scope, l = find_lesson(ns.arg)
    if l.get("status") != "open":
        raise OrchError("%s is %s, not open" % (ns.arg, l.get("status")), 1)
    dest = os.path.join(lesson_dir(d), "archive", os.path.basename(l.path))
    if os.path.exists(dest):
        raise OrchError("%s already exists" % dest, 1)
    out, done = [], False
    for line in (read_text(l.path) or "").split("\n"):
        m = FIELD_RE.match(line)
        if m and m.group(1) == "status" and not done:
            out += ["- status: archived", "- archived: %s \u2014 %s" % (now()[:10], why)]
            done = True
        else:
            out.append(line)
    if not done:
        raise OrchError("the lesson has no status line", 1)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    write_atomic(dest, "\n".join(out).encode("utf-8"), 0o644)
    os.unlink(l.path)
    write_index(d, scope)
    print("archived %s" % ns.arg)
    print("undo: orch lessons restore %s" % ns.arg)
    return 0


def cmd_restore(ns):
    if not ns.arg:
        raise OrchError("restore takes a lesson id", 1)
    require_write_allowed()
    d, scope, l = find_archived(ns.arg)
    if l.get("status") != "archived":
        raise OrchError("%s is %s, not archived (a promoted lesson stays where it was promoted)" % (ns.arg, l.get("status")), 1)
    dest = os.path.join(lesson_dir(d), os.path.basename(l.path))
    if os.path.exists(dest):
        raise OrchError("%s already exists" % dest, 1)
    out, st_done, ar_done = [], False, False
    for line in (read_text(l.path) or "").split("\n"):
        m = FIELD_RE.match(line)
        if m and m.group(1) == "status" and not st_done:
            out.append("- status: open")
            st_done = True
        elif m and m.group(1) == "archived" and not ar_done:
            ar_done = True
        else:
            out.append(line)
    write_atomic(dest, "\n".join(out).encode("utf-8"), 0o644)
    os.unlink(l.path)
    write_index(d, scope)
    print("restored %s" % ns.arg)
    return 0


def cmd_lessons(ns, extra):
    handler = {"add": cmd_add, "promote": cmd_promote, "index": cmd_index, "check": cmd_check,
               "migrate": cmd_migrate, "usage": cmd_usage, "audit": cmd_audit,
               "archive": cmd_archive, "restore": cmd_restore}[ns.action]
    if ns.action not in ("promote", "archive", "restore") and ns.arg:
        raise OrchError("%s takes no positional argument" % ns.action, 1)
    return handler(ns)


def register(add):
    sp = add("lessons", cmd_lessons, "lesson files: add | promote ID --to WHERE | index | check | migrate | usage | audit | archive ID --reason WHY | restore ID")
    sp._optionals.conflict_handler = "resolve"      # --task here is the lesson's `task:` field; -t stays the task selector
    sp.add_argument("action", choices=["add", "promote", "index", "check", "migrate", "usage", "audit", "archive", "restore"])
    sp.add_argument("arg", nargs="?", default=None, metavar="ID")
    sp.add_argument("--scope", default=None, help="add: project | global; index: restrict to one")
    sp.add_argument("--repo", default=None, help="the repo folder (add with --scope project; index)")
    sp.add_argument("--title", default=None)
    sp.add_argument("--role", default=None, help="who the lesson is for, e.g. implementer, planner + orchestrator")
    sp.add_argument("--summary", default=None, help="the instruction itself, one line, at most %d characters" % SUMMARY_MAX)
    sp.add_argument("--lesson", default=None, help="the full instruction")
    sp.add_argument("--quote", default=None, help='"<the user\'s words>" or none (observed: <where>)')
    sp.add_argument("--review-by", dest="review_by", default=None, help="YYYY-MM-DD")
    sp.add_argument("--exempt", default=None, help="why this lesson never expires (default: not exempt)")
    sp.add_argument("--task", dest="task_note", default=None, help="the task it came from")
    sp.add_argument("--what-happened", dest="what_happened", default=None)
    sp.add_argument("--actually-true", dest="actually_true", default=None)
    sp.add_argument("--to", default=None, help="promote: where the lesson went (a file, a rule)")
    sp.add_argument("--root", default=None, help="check: the tasks root (default: the orch root)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true", help="migrate: report, write nothing")
    sp.add_argument("--json", action="store_true", help="migrate --dry-run, usage, audit: JSON")
    sp.add_argument("--apply", action="store_true", help="migrate: convert the legacy LESSONS.md files")
    sp.add_argument("--summaries", default=None, help="migrate --apply: JSON file, id -> {summary, exempt}")
    sp.add_argument("--restore", default=None, metavar="BACKUP_DIR", help="migrate: undo an apply from its backup")
    sp.add_argument("--reason", default=None, help="archive: why the lesson goes out of the briefs")
    sp.add_argument("--reads", action="store_true", help="usage: also count Read calls on each file in the transcripts since the tracking date")

    sp = add("cite", cmd_cite, "record that a lesson line changed what you did (a `cite` journal line)")
    sp.add_argument("lesson", metavar="LESSON_ID", help="global/<name> or <repo>/<name>")
    sp.add_argument("--for", dest="for_text", required=True, help="what the lesson changed, one line")
    sp.add_argument("--as", dest="as_role", default=None, help="your role (recorded in the line)")
