"""orch memory audit|clear|restore|auto-off|auto-on|status: Claude Code's auto-memory, seen and changed from orch.

Every path here derives from `paths.claude_home()` (ORCH_CLAUDE_HOME or ~/.claude) or from `--project`; this module never
resolves a home directory itself. A project's memory folder is `<claude home>/projects/<folder>/memory/`, where <folder>
is the one whose transcripts record `--project` as their starting `cwd`.

Writes outside the orch data root are three: a project's memory folder (clear, restore), a project's
`.claude/settings.local.json` and `<claude home>/settings.json` (auto-off, auto-on). Invariants: nothing is removed
before a verified copy exists; no settings key other than `autoMemoryEnabled` changes; no settings content is printed
(settings files can hold secrets): a settings file is read in-process and only that one key's value is reported.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import shutil

from .paths import BIN_DIR, OrchError, backups_dir, claude_home, now, require_write_allowed, write_atomic

KEY = "autoMemoryEnabled"
ENV = "CLAUDE_CODE_DISABLE_AUTO_MEMORY"
REQUIRED = ("quote", "scope", "review-by", "exempt")
MANIFEST = "MANIFEST.json"
HEAD_LINES = 500          # lines read from a transcript's head to find its starting cwd and time

MODEL_RE = re.compile(r"\b(haiku|sonnet|opus|fable|mythos)\b", re.I)
TIER_RE = re.compile(r"\b(haiku|sonnet|opus|fable)/(low|medium|high|xhigh|max)\b", re.I)
ESCALATION_RE = re.compile(r"\bescalat(?:e|ed|es|ing|ion|ions)\b", re.I)


# ---- finding a project's folder -------------------------------------------------------------------------------------

def projects_dir():
    return os.path.join(claude_home(), "projects")


def transcripts(folder):
    return sorted(glob.glob(os.path.join(folder, "*.jsonl")))


def transcript_head(path):
    """(cwd, start epoch or None) from a transcript's first entries: the first `cwd` and the first `timestamp`."""
    cwd, start = None, None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= HEAD_LINES or (cwd and start is not None):
                    break
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(o, dict):
                    continue
                if cwd is None and isinstance(o.get("cwd"), str) and o["cwd"]:
                    cwd = o["cwd"]
                if start is None and isinstance(o.get("timestamp"), str):
                    start = parse_ts(o["timestamp"])
    except OSError:
        pass
    return cwd, start


def parse_ts(text):
    m = re.match(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?Z$", text)
    if not m:
        return None
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc).timestamp()


def folder_project(folder):
    """The starting cwd recorded by the folder's transcripts (the first one that has one), or None."""
    for t in transcripts(folder):
        cwd, _ = transcript_head(t)
        if cwd:
            return cwd
    return None


def project_folders(project):
    """Every `projects/*/` folder one of whose transcripts started in `project` (realpath on both sides)."""
    want = os.path.realpath(project)
    out = []
    for folder in sorted(glob.glob(os.path.join(projects_dir(), "*", ""))):
        folder = folder.rstrip(os.sep)
        for t in transcripts(folder):
            cwd, _ = transcript_head(t)
            if cwd and os.path.realpath(cwd) == want:
                out.append(folder)
                break
    return out


def one_folder(project):
    """The project's folder, or None when no transcript names it. More than one is refused (exit 2)."""
    found = project_folders(project)
    if len(found) > 1:
        raise OrchError("more than one Claude project folder records %s as its cwd: %s" % (
            project, ", ".join(os.path.basename(f) for f in found)), 2)
    return found[0] if found else None


def project_arg(ns):
    return os.path.realpath(ns.project or os.getcwd())


def no_memory(project):
    print("no Claude memory for this project (%s)" % project)
    return 0


# ---- audit ----------------------------------------------------------------------------------------------------------

def frontmatter(text):
    """{key: value} of a `---` block: top-level keys and the keys of one nested level (metadata:), flattened."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    out = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^\s*([A-Za-z0-9_-]+):\s*(.*?)\s*$", line)
        if not m:
            continue
        value = m.group(2)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not out.get(m.group(1)):
            out[m.group(1)] = value
    return out


def role_names():
    names = {"orchestrator"}
    for p in glob.glob(os.path.join(BIN_DIR, "..", "agents", "*.md")):
        names.add(os.path.splitext(os.path.basename(p))[0])
    return names


def policy_terms(text, roles):
    terms = []
    for rx in (TIER_RE, MODEL_RE, ESCALATION_RE):
        for m in rx.finditer(text):
            terms.append(m.group(0).lower())
    for r in sorted(roles, key=len, reverse=True):
        if re.search(r"(?<![\w-])(?:orch[:-])?%s(?![\w-])" % re.escape(r), text, re.I):
            terms.append(r)
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def walk_files(top):
    """Sorted relative paths of the regular files under `top`; anything else (a link, a device) is refused."""
    out = []
    for d, dirs, files in os.walk(top):
        dirs.sort()
        for n in list(dirs) + files:
            full = os.path.join(d, n)
            if os.path.islink(full):
                raise OrchError("%s is a symbolic link; refused (nothing changed)" % full, 2)
        for n in files:
            full = os.path.join(d, n)
            if not os.path.isfile(full):
                raise OrchError("%s is not a regular file; refused (nothing changed)" % full, 2)
            out.append(os.path.relpath(full, top))
    return sorted(out)


def mtime_date(path):
    return datetime.datetime.fromtimestamp(os.path.getmtime(path), datetime.timezone.utc).strftime("%Y-%m-%d")


def audit_folder(folder, project):
    from . import lessons
    mem = os.path.join(folder, "memory")
    verbs, roles = lessons.cli_verbs(), role_names()
    repo = project if project and os.path.isdir(project) else None
    rows = []
    for rel in walk_files(mem) if os.path.isdir(mem) else []:
        path = os.path.join(mem, rel)
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
        fm = frontmatter(text)
        index = rel == "MEMORY.md"
        row = {"file": rel, "name": None, "type": "index" if index else None, "description": None,
               "size": os.path.getsize(path), "modified": None, "mtime": mtime_date(path),
               "origin_transcript": None, "missing": [], "flags": []}
        if fm is not None:
            row.update(name=fm.get("name") or None, description=fm.get("description") or None,
                       modified=(fm.get("modified") or "")[:10] or None)
            row["type"] = fm.get("type") or row["type"]
            sid = fm.get("originSessionId")
            if sid:
                row["origin_transcript"] = "exists" if os.path.isfile(os.path.join(folder, sid + ".jsonl")) else "gone"
        if not index:
            row["missing"] = [k for k in REQUIRED if not (fm or {}).get(k)]
        for tok, why in lessons.stale_names(text, repo, verbs):
            row["flags"].append({"kind": "stale-name", "detail": "`%s` %s" % (tok, why)})
        terms = policy_terms(text, roles)
        if terms:
            row["flags"].append({"kind": "orch-policy", "detail": "names " + ", ".join(terms[:8])})
        rows.append(row)
    return {"project": project, "folder": folder, "memory": mem, "files": rows}


def print_audit(rep):
    print("project %s — %s: %d file%s" % (rep["project"] or "(unknown)", rep["memory"], len(rep["files"]),
                                         "" if len(rep["files"]) == 1 else "s"))
    if not rep["project"] or not os.path.isdir(rep["project"]):
        print("  (repo paths not checked: the project folder is not known or not on disk)")
    for r in rep["files"]:
        bits = [r["file"], "[%s]" % (r["type"] or "no type"), "%d B" % r["size"],
                "modified %s" % (r["modified"] or "-"), "mtime %s" % r["mtime"]]
        if r["origin_transcript"]:
            bits.append("origin transcript %s" % r["origin_transcript"])
        print("  " + "  ".join(bits))
        if r["description"]:
            d = " ".join(r["description"].split())
            print("      description: %s" % (d if len(d) <= 160 else d[:157] + "..."))
        if r["missing"]:
            print("      missing: %s" % ", ".join(r["missing"]))
        for f in r["flags"]:
            print("      %s: %s" % (f["kind"], f["detail"]))


def cmd_audit(ns):
    if ns.all:
        if ns.project:
            raise OrchError("audit takes --project or --all, not both", 1)
        reps = []
        for folder in sorted(glob.glob(os.path.join(projects_dir(), "*", ""))):
            folder = folder.rstrip(os.sep)
            mem = os.path.join(folder, "memory")
            if os.path.isdir(mem) and os.listdir(mem):
                reps.append(audit_folder(folder, folder_project(folder)))
        if ns.json:
            print(json.dumps({"projects": reps}, indent=2, ensure_ascii=False))
            return 0
        print("%d project%s with Claude memory" % (len(reps), "" if len(reps) == 1 else "s"))
        for rep in reps:
            print_audit(rep)
        return 0
    project = project_arg(ns)
    folder = one_folder(project)
    if folder is None:
        if ns.json:
            print(json.dumps({"project": project, "folder": None, "files": []}, indent=2))
            return 0
        return no_memory(project)
    rep = audit_folder(folder, project)
    if ns.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
    else:
        print_audit(rep)
    return 0


# ---- clear and restore ----------------------------------------------------------------------------------------------

def copy_file(src, dst):
    """One file copy (contents and mode). A seam for the tests' corrupted-copy case."""
    shutil.copy2(src, dst)


def memory_backups():
    return os.path.join(backups_dir(), "memory")


def unused_path(path):
    out, n = path, 1
    while os.path.exists(out):
        n += 1
        out = "%s-%d" % (path, n)
    return out


def cmd_clear(ns):
    reason = " ".join((ns.reason or "").split())
    if not reason:
        raise OrchError('clear needs --reason "<why>"', 1)
    if not ns.project:
        raise OrchError("clear needs --project P", 1)
    require_write_allowed()
    project = project_arg(ns)
    folder = one_folder(project)
    if folder is None:
        return no_memory(project)
    mem = os.path.join(folder, "memory")
    if not os.path.isdir(mem):
        raise OrchError("%s does not exist; nothing to clear" % mem, 2)
    files = walk_files(mem)
    if not files:
        raise OrchError("%s is empty; nothing to clear" % mem, 2)
    hashes = {rel: file_hash(os.path.join(mem, rel)) for rel in files}
    stamp = now().replace("-", "").replace(":", "")
    dest = unused_path(os.path.join(memory_backups(), "%s-%s" % (stamp, os.path.basename(folder))))
    os.makedirs(dest)
    for d, dirs, _ in os.walk(mem):
        for n in dirs:
            os.makedirs(os.path.join(dest, os.path.relpath(os.path.join(d, n), mem)), exist_ok=True)
    for rel in files:
        copy_file(os.path.join(mem, rel), os.path.join(dest, rel))
    manifest = {"project": project, "folder": mem, "reason": reason, "date": now(), "files": hashes}
    write_atomic(os.path.join(dest, MANIFEST), (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                 0o644)
    copied = [r for r in walk_files(dest) if r != MANIFEST]
    if sorted(copied) != files:
        raise OrchError("the backup %s does not hold the same files as %s; nothing removed" % (dest, mem), 2)
    for rel in files:
        if file_hash(os.path.join(dest, rel)) != hashes[rel]:
            raise OrchError("the backup copy of %s does not match the original; nothing removed (backup %s)" % (rel, dest), 2)
    for rel in files:
        p = os.path.join(mem, rel)
        if not os.path.isfile(p) or file_hash(p) != hashes[rel]:
            raise OrchError("%s changed during the backup; nothing removed (backup %s)" % (rel, dest), 2)
    removed, kept = 0, []
    for rel in files:
        p = os.path.join(mem, rel)
        if file_hash(p) != hashes[rel]:        # changed since it was copied: keep it
            kept.append(rel)
            continue
        os.remove(p)
        removed += 1
    for d, dirs, fs in sorted(os.walk(mem, topdown=False), key=lambda x: -len(x[0])):
        if d != mem and not os.listdir(d):
            os.rmdir(d)
    left = [n for n in os.listdir(mem)]
    print("cleared %d file%s from %s; backup %s" % (removed, "" if removed == 1 else "s", mem, dest))
    if kept or left:
        print("kept (changed or added after the copy): %s" % ", ".join(sorted(set(kept) | set(left))))
    print("undo: orch memory restore %s" % os.path.basename(dest))
    return 0


def cmd_restore(ns):
    name = ns.arg
    if not name or name in (".", "..") or os.sep in name or (os.altsep and os.altsep in name):
        raise OrchError("restore takes a backup name (the last part of the path clear printed)", 1)
    require_write_allowed()
    bdir = os.path.normpath(os.path.join(memory_backups(), name))
    try:
        with open(os.path.join(bdir, MANIFEST), "rb") as fh:
            manifest = json.loads(fh.read().decode("utf-8"))
        target, hashes = manifest["folder"], dict(manifest["files"])
    except (OSError, ValueError, KeyError, TypeError):
        raise OrchError("%s is not a memory backup (no readable %s)" % (bdir, MANIFEST), 1)
    root = os.path.realpath(projects_dir())
    real = os.path.realpath(target)
    if os.path.basename(real) != "memory" or os.path.dirname(os.path.dirname(real)) != root:
        raise OrchError("the backup's folder %s is not a memory folder under %s; refused" % (target, root), 2)
    for rel, h in sorted(hashes.items()):
        src = os.path.normpath(os.path.join(bdir, rel))
        if not src.startswith(bdir + os.sep) or os.path.isabs(rel):
            raise OrchError("the manifest names a path outside the backup: %r" % rel, 2)
        if not os.path.isfile(src) or file_hash(src) != h:
            raise OrchError("the backup's %s does not match its manifest; nothing restored" % rel, 2)
    if os.path.isdir(target) and os.listdir(target) and not ns.merge:
        raise OrchError("%s is not empty; restore --merge adds the missing files and never overwrites one" % target, 2)
    restored, skipped = 0, []
    for rel in sorted(hashes):
        dst = os.path.join(target, rel)
        if os.path.lexists(dst):
            skipped.append(rel)
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        src = os.path.join(bdir, rel)
        with open(src, "rb") as fh:
            data = fh.read()
        write_atomic(dst, data, os.stat(src).st_mode & 0o7777)
        restored += 1
    print("restored %d file%s to %s" % (restored, "" if restored == 1 else "s", target))
    if skipped:
        print("kept %d existing file%s: %s" % (len(skipped), "" if len(skipped) == 1 else "s", ", ".join(skipped)))
    return 0


# ---- the auto-memory switch -----------------------------------------------------------------------------------------

def local_settings(project):
    return os.path.join(project, ".claude", "settings.local.json")


def shared_settings(project):
    return os.path.join(project, ".claude", "settings.json")


def global_settings():
    return os.path.join(claude_home(), "settings.json")


def read_object(path):
    """(text, object) of a JSON settings file; (None, None) when it is absent. Malformed → refused, untouched.
    Error messages name the file and the position only, never its content."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        return None, None
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except UnicodeDecodeError:
        raise OrchError("%s is not UTF-8; refused (file untouched)" % path, 2)
    except ValueError as exc:
        where = " (line %d, column %d)" % (exc.lineno, exc.colno) if hasattr(exc, "lineno") else ""
        raise OrchError("%s is not valid JSON%s; refused (file untouched)" % (path, where), 2)
    if not isinstance(data, dict):
        raise OrchError("%s is not a JSON object; refused (file untouched)" % path, 2)
    return text, data


def indent_of(text):
    m = re.search(r"\n([ \t]+)\"", text or "")
    return m.group(1) if m else 2


def cmd_switch(ns, on):
    if bool(ns.project) == bool(ns.glob):
        raise OrchError("%s takes --project P or --global" % ns.action, 1)
    if ns.project:
        project = os.path.realpath(ns.project)
        if not os.path.isdir(project):
            raise OrchError("%s is not a folder" % project, 1)
        path = local_settings(project)
    else:
        path = global_settings()
    require_write_allowed()
    path = os.path.realpath(path) if os.path.lexists(path) else path     # a linked settings file: write its target
    text, data = read_object(path)
    if on:
        if data is None or KEY not in data:
            print("%s: %s not set (unchanged)" % (path, KEY))
            return 0
        del data[KEY]
        new_value = "removed (Claude's default applies)"
    else:
        if data is not None and data.get(KEY) is False:
            print("%s: %s = false (unchanged)" % (path, KEY))
            return 0
        data = {} if data is None else data
        data[KEY] = False                      # an existing key keeps its place; a new one goes last
        new_value = "= false"
    out = json.dumps(data, indent=indent_of(text), ensure_ascii=False) + "\n"
    if text is None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_atomic(path, out.encode("utf-8"), 0o600)
    else:
        write_atomic(path, out.encode("utf-8"))   # keeps the file's mode
    print("%s: %s %s" % (path, KEY, new_value))
    return 0


def setting_value(path):
    """The one key's value as a word; never anything else from the file."""
    try:
        _, data = read_object(path)
    except OrchError:
        return "unreadable (not a JSON object)"
    if data is None:
        return "no file"
    if KEY not in data:
        return "unset"
    v = data[KEY]
    return "true" if v is True else "false" if v is False else "set (not a boolean)"


def env_value():
    v = os.environ.get(ENV)
    if v is None:
        return "unset"
    return v if re.match(r"^[A-Za-z0-9]{0,8}$", v) else "set"


def names_memory(path, memory_md):
    """True when the transcript has an `instructions` attachment that lists `memory_md` among its files."""
    want = {memory_md, os.path.realpath(memory_md)}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"instructions"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                a = o.get("attachment") if isinstance(o, dict) and o.get("type") == "attachment" else None
                if not isinstance(a, dict) or a.get("type") != "instructions":
                    continue
                for f in a.get("files") or []:
                    if isinstance(f, dict) and f.get("path") in want:
                        return True
    except OSError:
        pass
    return False


def iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def live_check(folder, setting_files):
    if folder is None:
        return {"state": "not yet verified", "why": "no Claude project folder records this project"}
    changed = max([os.path.getmtime(p) for p in setting_files if os.path.isfile(p)] or [0])
    newest = None
    for t in transcripts(folder):
        _, start = transcript_head(t)
        if start is None:
            start = os.path.getmtime(t)
        if start > changed and (newest is None or start > newest[1]):
            newest = (t, start)
    since = iso(changed) if changed else None
    if newest is None:
        return {"state": "not yet verified", "why": "no session started after the settings change", "since": since}
    memory_md = os.path.join(folder, "memory", "MEMORY.md")
    state = "still loading" if names_memory(newest[0], memory_md) else "verified off"
    return {"state": state, "transcript": os.path.basename(newest[0]), "started": iso(newest[1]), "since": since}


def cmd_status(ns):
    project = project_arg(ns)
    files = [("project local", local_settings(project)), ("project shared", shared_settings(project)),
             ("global", global_settings())]
    folder = one_folder(project)
    rep = {"project": project, "folder": folder,
           "settings": [{"source": s, "file": p, "value": setting_value(p)} for s, p in files],
           "env": {"name": ENV, "value": env_value()},
           "live": live_check(folder, [p for _, p in files])}
    if ns.json:
        print(json.dumps(rep, indent=2))
        return 0
    print("project %s" % project)
    for s in rep["settings"]:
        print("  %-15s %s: %s" % (s["source"], s["file"], s["value"]))
    print("  %-15s %s: %s" % ("env", ENV, rep["env"]["value"]))
    lv = rep["live"]
    if "transcript" in lv:
        print("  live check: %s (%s, started %s, after the settings change at %s)" % (
            lv["state"], lv["transcript"], lv["started"], lv["since"] or "-"))
    else:
        print("  live check: %s (%s)" % (lv["state"], lv["why"]))
    return 0


def cmd_memory(ns, extra):
    if ns.action != "restore" and ns.arg:
        raise OrchError("%s takes no positional argument" % ns.action, 1)
    if ns.action not in ("audit",) and ns.all:
        raise OrchError("--all is for audit only", 1)
    if ns.action not in ("auto-off", "auto-on") and ns.glob:
        raise OrchError("--global is for auto-off and auto-on only", 1)
    if ns.action != "clear" and ns.reason:
        raise OrchError("--reason is for clear only", 1)
    if ns.action != "restore" and ns.merge:
        raise OrchError("--merge is for restore only", 1)
    if ns.action == "restore" and ns.project:
        raise OrchError("restore takes the backup name only (the folder is in its manifest)", 1)
    if ns.json and ns.action not in ("audit", "status"):
        raise OrchError("--json is for audit and status only", 1)
    return {"audit": cmd_audit, "clear": cmd_clear, "restore": cmd_restore, "status": cmd_status,
            "auto-off": lambda n: cmd_switch(n, False), "auto-on": lambda n: cmd_switch(n, True)}[ns.action](ns)


def register(add):
    sp = add("memory", cmd_memory, "Claude's auto-memory: audit [--project P | --all] | clear --project P --reason WHY | "
             "restore NAME [--merge] | auto-off|auto-on --project P | --global | status [--project P]")
    sp.add_argument("action", choices=["audit", "clear", "restore", "auto-off", "auto-on", "status"])
    sp.add_argument("arg", nargs="?", default=None, metavar="NAME", help="restore: the backup name clear printed")
    sp.add_argument("--project", default=None, help="the project folder (default: the current directory)")
    sp.add_argument("--all", action="store_true", help="audit: every project with Claude memory")
    sp.add_argument("--json", action="store_true", help="audit, status: JSON")
    sp.add_argument("--reason", default=None, help="clear: why (kept in the backup's manifest)")
    sp.add_argument("--merge", action="store_true", help="restore: add the missing files, never overwrite one")
    sp.add_argument("--global", dest="glob", action="store_true", help="auto-off, auto-on: <claude home>/settings.json")
