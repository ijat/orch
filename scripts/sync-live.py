#!/usr/bin/env python3
"""Write the live ~/.claude orch install from a commit of this repo plus a private overlay.

The repo is the source of truth; scripts/export.py maps a live install back to the repo's form, and
this script is its inverse. It builds the new live image in a temp stage (the commit's files, the
overlay's edits applied) and reports what would change.

Usage: scripts/sync-live.py [--dry-run] [--rev REV] [--live DIR] [--overlay FILE] [--private FILE]
                            [--denylist FILE]

- --live      default ~/.claude (the live install)
- --overlay   default <live>/orch-live-overlay.json; owner-only (chmod 600), outside <repo>
- --private   default <live>/orch-export-private.json (the @@KEY@@ values export.py uses)
- --denylist  default <live>/orch-export-denylist.txt
- --rev       the commit to sync, default HEAD. With the default, a working tree that is not clean
              (tracked changes or untracked files) is a refusal, so what was tested is what is synced.
- --dry-run   report only. Without it this version exits 2 (the write phase is not implemented yet).

<repo> is this script's grandparent directory. The file set is the one the commit's own
scripts/export.py defines (FILES, TREES, MOVED), imported by path with bytecode off.

Overlay (JSON): {"version": 1, "edits": [{"path", "public", "live"}], "protect": [prefix],
"checks": [{"name", "argv"}]}. Each edit is literal, keyed by repo path, applied in order, and its
`public` text must occur exactly once. A managed path under a `protect` prefix is allowed only if it
is a MOVED target. `{live}` is the only placeholder allowed in a check's argv.

Gates (step 6): before anything is reported as syncable, the stage must pass four checks; each
failure is a `REFUSE:` line and all of them are collected:
- syntax: `bash -n` for a `.sh` file or a `#!` naming bash; an in-process compile() for a `.py` file or
  a `#!` naming python;
- agents: `python3 -I -B <snapshot>/bin/orch-build-agents.py <root>/skills/orchestrate/roles
  <root>/agents --check` exits 0 (a stale render: run `python3 bin/orch-build-agents.py
  skills/orchestrate/roles agents` in the repo and commit);
- checks: each overlay check runs without a shell, with ORCH_HOME=<root>, first on live (the
  baseline), then on the stage. A non-zero exit fails, and so does a `WARN` line the baseline did not
  print (the root path and its realpath are normalised to `<root>` first);
- round trip: the commit's own export.py, run in a copy of the commit with `--src <stage>`, must
  reproduce the commit exactly (file set, bytes, owner-exec bit), its denylist scan included.
Every subprocess gets PYTHONDONTWRITEBYTECODE=1; Python ones also run with -I -B.

SYNC_LIVE_CHECK_TIMEOUT (seconds, default 300) is for the test suite only: it shortens the per-check
timeout so a hanging check can be tested. Do not set it in normal use.

Output: `create <path>` / `update <path>` lines, a unified diff per update (`--- live/<path>`,
`+++ new/<path>`), `REFUSE: <reason>` lines, then
`sync-live: dry run, rev <short>: C create, U update, N unchanged, R refusal(s)` or
`sync-live: dry run, rev <short>: nothing to do`.

POSIX only. Exit 0 = would sync; 1 = refused, nothing written; 2 = bad input or unsafe config;
3 = reserved. Nothing under --live or <repo> is created, changed or deleted by a dry run.
"""
import argparse, difflib, hashlib, importlib.util, io, json, os, posixpath, re, shutil, signal, stat
import subprocess, sys, tarfile, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_DIRS = ("skills", "agents", "bin")
NO_EDIT_PREFIXES = ("agents/", "skills/orchestrate/roles/")
EXPORT_NAMES = ("FILES", "TREES", "MOVED", "tree_files", "fill", "load_private")


class Fail(Exception):
    """Stop with exit code `code` and message `msg`."""
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def bad(msg):
    return Fail(2, msg)


def git_env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def git(repo, *args):
    """stdout bytes of `git -C repo args`; None on a non-zero exit."""
    try:
        r = subprocess.run(["git", "-C", repo, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=git_env())
    except OSError as e:
        raise bad("git: %s" % e)
    return r.stdout if r.returncode == 0 else None


def under(path, root):
    """True if `path` is `root` or inside it (both already real paths)."""
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def has_prefix(rel, prefix):
    p = prefix.rstrip("/")
    return rel == p or rel.startswith(p + "/")


def sha_exec(path):
    with open(path, "rb") as f:
        data = f.read()
    return hashlib.sha256(data).hexdigest(), bool(os.stat(path).st_mode & stat.S_IXUSR)


# ---------- 0 + 4: config files and overlay ----------

def check_config_files(paths):
    for p in paths:
        if not os.path.isfile(p):
            raise bad("missing config file: %s" % p)


def load_overlay(path, repo):
    """The validated overlay dict (edits' paths are checked later, against the managed set)."""
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        raise bad("overlay %s: not a regular file" % path)
    if st.st_uid != os.getuid():
        raise bad("overlay %s: not owned by the current user" % path)
    if st.st_mode & 0o077:
        raise bad("overlay %s: readable by others; run chmod 600 %s" % (path, path))
    if under(os.path.realpath(path), os.path.realpath(repo)):
        raise bad("overlay %s: lies inside the repo; keep it outside" % path)
    try:
        with open(path, encoding="utf-8") as f:
            ov = json.load(f)
    except (OSError, ValueError) as e:
        raise bad("overlay %s: not valid JSON (%s)" % (path, e.__class__.__name__))
    if not isinstance(ov, dict):
        raise bad("overlay: not a JSON object")
    extra = set(ov) - {"version", "edits", "protect", "checks"}
    if extra:
        raise bad("overlay: unknown key(s): %s" % ", ".join(sorted(extra)))
    if ov.get("version") != 1 or isinstance(ov.get("version"), bool):
        raise bad("overlay: version must be 1")
    edits = ov.get("edits", [])
    if not isinstance(edits, list):
        raise bad("overlay: edits must be a list")
    for i, e in enumerate(edits):
        if not isinstance(e, dict) or set(e) != {"path", "public", "live"}:
            raise bad("overlay: edits[%d] must have exactly the keys path, public, live" % i)
        if not all(isinstance(e[k], str) for k in ("path", "public", "live")):
            raise bad("overlay: edits[%d]: path, public and live must be strings" % i)
        if not e["public"]:
            raise bad("overlay: edits[%d].public is empty" % i)
        if e["public"] == e["live"]:
            raise bad("overlay: edits[%d].public equals its live text" % i)
    protect = ov.get("protect", [])
    if not isinstance(protect, list):
        raise bad("overlay: protect must be a list")
    for i, p in enumerate(protect):
        if (not isinstance(p, str) or not p or p.startswith("/") or ".." in p.split("/")
                or posixpath.normpath(p.rstrip("/")) != p.rstrip("/")):
            raise bad("overlay: protect[%d] must be a relative, normalised prefix" % i)
    checks = ov.get("checks", [])
    if not isinstance(checks, list):
        raise bad("overlay: checks must be a list")
    for i, c in enumerate(checks):
        if not isinstance(c, dict) or set(c) != {"name", "argv"}:
            raise bad("overlay: checks[%d] must have exactly the keys name, argv" % i)
        if not isinstance(c["name"], str) or not c["name"]:
            raise bad("overlay: checks[%d].name must be a non-empty string" % i)
        argv = c["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise bad("overlay: checks[%d].argv must be a non-empty list of strings" % i)
        for a in argv:
            for ph in re.findall(r"\{([^{}]*)\}", a):
                if ph != "live":
                    raise bad("overlay: checks[%d].argv: placeholder {%s} (only {live} is allowed)" % (i, ph))
    return {"edits": edits, "protect": protect, "checks": checks}


def check_edit_paths(edits, managed_repo_paths):
    for i, e in enumerate(edits):
        if e["path"].startswith(NO_EDIT_PREFIXES):
            raise bad("overlay: edits[%d].path %s: agents and role sources take no edits" % (i, e["path"]))
        if e["path"] not in managed_repo_paths:
            raise bad("overlay: edits[%d].path %s is not a managed repo path" % (i, e["path"]))


# ---------- 1: source ----------

def resolve_rev(repo, rev):
    """(full sha, short sha) of `rev`'s commit."""
    full = git(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}")
    short = git(repo, "rev-parse", "--short", rev + "^{commit}")
    if not full or not short:
        raise bad("bad rev: %s" % rev)
    return full.decode().strip(), short.decode().strip()


def tree_dirty(repo):
    out = git(repo, "status", "--porcelain")
    if out is None:
        raise bad("git status failed in %s" % repo)
    return bool(out.strip())


def snapshot(repo, rev, dst):
    """Extract `git archive <rev>` into `dst`; return {repo_rel: exec_bool} for every file."""
    data = git(repo, "archive", "--format=tar", rev)
    if data is None:
        raise bad("git archive failed for %s" % rev)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        members = t.getmembers()
        for m in members:
            name = m.name.rstrip("/")
            if not (m.isfile() or m.isdir()):
                raise bad("snapshot: %s is neither a file nor a directory" % m.name)
            if name.startswith("/") or ".." in name.split("/") or posixpath.normpath(name) != name:
                raise bad("snapshot: unsafe member name %r" % m.name)
            if m.isfile():
                files[name] = bool(m.mode & 0o100)
        kw = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
        t.extractall(dst, members=members, **kw)
    return files


# ---------- 2 + 3: file set and path guards ----------

def load_export(path):
    """The snapshot's export.py as a module, imported with bytecode off."""
    if not os.path.isfile(path):
        raise bad("snapshot has no scripts/export.py")
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("sync_live_export", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:  # any failure of a foreign module is bad input, never a crash
        raise bad("importing the snapshot's export.py failed: %s: %s" % (e.__class__.__name__, e))
    finally:
        sys.dont_write_bytecode = old
    missing = [n for n in EXPORT_NAMES if not hasattr(mod, n)]
    if missing:
        raise bad("the snapshot's export.py lacks: %s" % ", ".join(missing))
    return mod


def managed_set(ex, snap, private):
    """([(live_rel, repo_rel)], moved live paths). Paths are not guarded yet."""
    pairs = [(f, f) for f in ex.FILES]
    moved = set()
    for key, repo_rel in ex.MOVED.items():
        live_rel = ex.fill(key, private)
        if live_rel is None:
            raise bad("private key missing for the moved file %s (%s)" % (repo_rel, key))
        pairs.append((live_rel, repo_rel))
        moved.add(live_rel)
    for t in ex.TREES:
        pairs += [(t + "/" + r, t + "/" + r) for r in ex.tree_files(os.path.join(snap, t))]
    seen, seen_repo = set(), set()
    for live_rel, repo_rel in pairs:
        if live_rel in seen:
            raise bad("duplicate managed live path: %s" % live_rel)
        if repo_rel in seen_repo:
            raise bad("duplicate managed repo path: %s" % repo_rel)
        seen.add(live_rel); seen_repo.add(repo_rel)
    return pairs, moved


def guard_paths(pairs, moved, protect, live):
    for live_rel, _ in pairs:
        if not isinstance(live_rel, str) or not live_rel:
            raise bad("unsafe managed path %r: empty" % (live_rel,))
        if live_rel.startswith("/") or posixpath.normpath(live_rel) != live_rel:
            raise bad("unsafe managed path %s: not relative and normalised" % live_rel)
        parts = live_rel.split("/")
        if any(p == ".." or p.startswith(".") for p in parts):
            raise bad("unsafe managed path %s: has a '..' or dot component" % live_rel)
        if parts[0] not in TOP_DIRS:
            raise bad("unsafe managed path %s: must start with %s" % (live_rel, ", ".join(TOP_DIRS)))
        for i in range(1, len(parts) + 1):
            p = os.path.join(live, *parts[:i])
            if os.path.islink(p):
                raise bad("unsafe managed path %s: %s is a symlink" % (live_rel, p))
        if live_rel not in moved and any(has_prefix(live_rel, pr) for pr in protect):
            raise bad("unsafe managed path %s: under a protected prefix" % live_rel)


def check_in_snapshot(pairs, snap_files):
    for _, repo_rel in pairs:
        if repo_rel not in snap_files:
            raise bad("managed repo path missing from the snapshot: %s" % repo_rel)


# ---------- 5: stage ----------

def build_stage(ex, pairs, snap, snap_files, edits, live, stage):
    by_path = {}
    for i, e in enumerate(edits):
        by_path.setdefault(e["path"], []).append((i, e))
    for live_rel, repo_rel in pairs:
        with open(os.path.join(snap, repo_rel), "rb") as f:
            data = f.read()
        if repo_rel in by_path:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                raise bad("%s: not valid UTF-8, cannot apply overlay edits" % repo_rel)
            for i, e in by_path[repo_rel]:
                n = text.count(e["public"])
                if n != 1:
                    raise bad("overlay edits[%d] on %s: public text found %d times (need exactly 1)"
                              % (i, repo_rel, n))
                text = text.replace(e["public"], e["live"])
            data = text.encode("utf-8")
        write_file(os.path.join(stage, live_rel), data, snap_files[repo_rel])
    managed = {lr for lr, _ in pairs}
    extras = []
    for t in ex.TREES:
        for r in ex.tree_files(os.path.join(live, t)):
            rel = t + "/" + r
            if rel not in managed:
                src = os.path.join(live, rel)
                with open(src, "rb") as f:
                    data = f.read()
                write_file(os.path.join(stage, rel), data, bool(os.stat(src).st_mode & stat.S_IXUSR))
                extras.append(rel)
    return extras


def write_file(path, data, exe):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    os.chmod(path, 0o755 if exe else 0o644)


# ---------- 6: gates (pure functions of the root they check; step 4 reuses them on live) ----------

GATE_TIMEOUT = 300
BUILD_CMD = "python3 bin/orch-build-agents.py skills/orchestrate/roles agents"


def check_timeout():
    """The per-check timeout; SYNC_LIVE_CHECK_TIMEOUT overrides it in tests only."""
    v = os.environ.get("SYNC_LIVE_CHECK_TIMEOUT")
    if v is None:
        return GATE_TIMEOUT
    try:
        t = float(v)
    except ValueError:
        t = 0
    if not t > 0:
        raise bad("SYNC_LIVE_CHECK_TIMEOUT must be a positive number of seconds")
    return t


def run_cmd(argv, env, timeout, cwd):
    """(rc, stdout, stderr) as text. Raises subprocess.TimeoutExpired after killing the whole process
    group, so a check's children cannot keep the pipes open; raises OSError if argv[0] cannot run."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                         env=env, cwd=cwd, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def tail(text, n=8):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return " | ".join(lines[-n:]) if lines else "(no output)"


def image_rels(ex, pairs, root):
    """Every file of the image under `root`: the managed live paths plus what the trees take there."""
    rels = [lr for lr, _ in pairs]
    seen = set(rels)
    for t in ex.TREES:
        d = os.path.join(root, t)
        if os.path.isdir(d):
            for r in ex.tree_files(d):
                rel = t + "/" + r
                if rel not in seen:
                    rels.append(rel); seen.add(rel)
    return rels


def gate_syntax(root, rels, tmp):
    """(a) bash -n for shell files, compile() for Python files."""
    refusals = []
    for rel in rels:
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as f:
            data = f.read()
        first = data.split(b"\n", 1)[0] if data.startswith(b"#!") else b""
        if rel.endswith(".sh") or b"bash" in first:
            try:
                rc, out, err = run_cmd(["bash", "-n", p], git_env(), GATE_TIMEOUT, tmp)
            except (OSError, subprocess.TimeoutExpired) as e:
                refusals.append("syntax: %s: bash -n could not run (%s)" % (rel, e.__class__.__name__))
                continue
            if rc:
                refusals.append("syntax: %s: bash -n: %s" % (rel, tail(err.replace(p, rel), 3)))
        elif rel.endswith(".py") or b"python" in first:
            try:
                compile(data, rel, "exec", dont_inherit=True)
            except (SyntaxError, ValueError) as e:
                refusals.append("syntax: %s: %s: %s" % (rel, e.__class__.__name__, e))
    return refusals


def gate_agents(snap, root, tmp):
    """(b) The rendered agents under `root` match their role sources (the snapshot's builder, --check)."""
    builder = os.path.join(snap, "bin", "orch-build-agents.py")
    if not os.path.isfile(builder):
        raise bad("snapshot has no bin/orch-build-agents.py")
    argv = [sys.executable, "-I", "-B", builder, os.path.join(root, "skills", "orchestrate", "roles"),
            os.path.join(root, "agents"), "--check"]
    try:
        rc, out, err = run_cmd(argv, git_env(), GATE_TIMEOUT, tmp)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ["agents: orch-build-agents.py --check could not run (%s)" % e.__class__.__name__]
    if rc:
        return ["agents: rendered agents are stale (%s); run `%s` in the repo and commit"
                % (tail(out + err), BUILD_CMD)]
    return []


def normalise(line, root):
    """`line` with `root` and its realpath replaced by <root>, longest first."""
    for r in sorted({root, os.path.realpath(root)}, key=len, reverse=True):
        line = line.replace(r, "<root>")
    return line.rstrip()


def run_check(check, root, live, tmp):
    """(rc, set of normalised WARN lines) of one overlay check with ORCH_HOME=root.
    Raises OSError or subprocess.TimeoutExpired."""
    argv = [a.replace("{live}", live) for a in check["argv"]]
    env = dict(os.environ, ORCH_HOME=root, PYTHONDONTWRITEBYTECODE="1")
    rc, out, err = run_cmd(argv, env, check_timeout(), tmp)
    warns = {normalise(l, root) for l in (out + err).splitlines() if l.startswith("WARN")}
    return rc, warns


def baseline_checks(checks, live, tmp):
    """([WARN set, or None if the check fails on live] aligned with `checks`, refusals). Computed once
    per run and reused for every later root."""
    base, refusals = [None] * len(checks), []
    for i, c in enumerate(checks):
        try:
            rc, warns = run_check(c, live, live, tmp)
        except subprocess.TimeoutExpired:
            refusals.append("check %s fails on current live: timed out" % c["name"]); continue
        except OSError as e:
            refusals.append("check %s fails on current live: cannot run (%s)" % (c["name"], e)); continue
        if rc:
            refusals.append("check %s fails on current live (exit %d)" % (c["name"], rc)); continue
        base[i] = warns
    return base, refusals


def gate_checks(checks, root, live, baseline, tmp):
    """(c) Each check on `root`: a non-zero exit, or a WARN line the baseline lacks, is a refusal.
    A check that already failed on live (baseline None) was refused there and is not re-run."""
    if len(baseline) != len(checks):
        raise bad("internal: the check baseline does not match the overlay's checks")
    refusals = []
    for c, base in zip(checks, baseline):
        name = c["name"]
        if base is None:
            continue
        try:
            rc, warns = run_check(c, root, live, tmp)
        except subprocess.TimeoutExpired:
            refusals.append("check %s: timed out" % name); continue
        except OSError as e:
            refusals.append("check %s: cannot run (%s)" % (name, e)); continue
        if rc:
            refusals.append("check %s: exit %d" % (name, rc))
        for w in sorted(warns - base):
            refusals.append("check %s: new %s" % (name, w))
    return refusals


def tree_diff(snap, snap_files, other):
    """Repo paths where `other` differs from the commit: file set, bytes, or owner-exec bit."""
    found = set()
    for cur, _dirs, names in os.walk(other):
        for n in names:
            p = os.path.join(cur, n)
            found.add(os.path.relpath(p, other).replace(os.sep, "/"))
    diffs = []
    for rel in sorted(found | set(snap_files)):
        if rel not in found or rel not in snap_files:
            diffs.append(rel); continue
        p = os.path.join(other, rel)
        if os.path.islink(p) or bool(os.stat(p).st_mode & stat.S_IXUSR) != snap_files[rel]:
            diffs.append(rel); continue
        with open(p, "rb") as f, open(os.path.join(snap, rel), "rb") as g:
            if f.read() != g.read():
                diffs.append(rel)
    return diffs


def gate_roundtrip(snap, snap_files, root, private_path, deny_path, tmp):
    """(d) The commit's export.py, run in a copy of the commit with --src root, reproduces the commit."""
    work = tempfile.mkdtemp(prefix="rt-", dir=tmp)
    copy = os.path.join(work, "repo")
    shutil.copytree(snap, copy)
    argv = [sys.executable, "-I", "-B", os.path.join(copy, "scripts", "export.py"), "--src", root,
            "--denylist", deny_path, "--private", private_path]
    try:
        rc, out, err = run_cmd(argv, git_env(), GATE_TIMEOUT, work)
    except (OSError, subprocess.TimeoutExpired) as e:
        return ["round trip: export.py could not run (%s)" % e.__class__.__name__]
    if rc:
        return ["round trip: export.py exit %d: %s" % (rc, tail(err))]
    return ["round trip: %s differs" % rel for rel in tree_diff(snap, snap_files, copy)]


def run_gates(ex, pairs, snap, snap_files, overlay, private_path, deny_path, root, live, baseline, tmp):
    """All four gates on `root`; the collected refusals."""
    refusals = gate_syntax(root, image_rels(ex, pairs, root), tmp)
    refusals += gate_agents(snap, root, tmp)
    refusals += gate_checks(overlay["checks"], root, live, baseline, tmp)
    refusals += gate_roundtrip(snap, snap_files, root, private_path, deny_path, tmp)
    return refusals


# ---------- 7 + 8: ops and output ----------

def compute_ops(pairs, stage, live):
    ops = []
    for live_rel, _ in pairs:
        lp = os.path.join(live, live_rel)
        if not os.path.lexists(lp):
            ops.append(("create", live_rel)); continue
        if not os.path.isfile(lp):
            raise bad("live path %s exists but is not a regular file" % lp)
        ops.append(("update" if sha_exec(lp) != sha_exec(os.path.join(stage, live_rel)) else "unchanged",
                    live_rel))
    return ops


def diff_text(old_path, new_path, rel):
    with open(old_path, "rb") as f:
        old = f.read()
    with open(new_path, "rb") as f:
        new = f.read()
    out = []
    if old != new:
        try:
            a, b = old.decode("utf-8"), new.decode("utf-8")
        except UnicodeDecodeError:
            out.append("binary differs\n")
        else:
            d = difflib.unified_diff(a.splitlines(True), b.splitlines(True), "live/" + rel, "new/" + rel)
            for line in d:
                out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    ox = bool(os.stat(old_path).st_mode & stat.S_IXUSR)
    nx = bool(os.stat(new_path).st_mode & stat.S_IXUSR)
    if ox != nx:
        out.append("exec bit differs: live %s, new %s\n" % ("on" if ox else "off", "on" if nx else "off"))
    return "".join(out)


def report(ops, refusals, short, stage, live):
    counts = {"create": 0, "update": 0, "unchanged": 0}
    for op, rel in ops:
        counts[op] += 1
        if op == "create":
            print("create %s" % rel)
        elif op == "update":
            print("update %s" % rel)
            sys.stdout.write(diff_text(os.path.join(live, rel), os.path.join(stage, rel), rel))
    for r in refusals:
        print("REFUSE: %s" % r)
    if not refusals and not counts["create"] and not counts["update"]:
        print("sync-live: dry run, rev %s: nothing to do" % short)
    else:
        print("sync-live: dry run, rev %s: %d create, %d update, %d unchanged, %d refusal(s)"
              % (short, counts["create"], counts["update"], counts["unchanged"], len(refusals)))
    return 0 if not refusals else 1


# ---------- main ----------

def parse_args(argv):
    ap = argparse.ArgumentParser(description="Sync the live orch install from this repo.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rev")
    ap.add_argument("--live")
    ap.add_argument("--overlay")
    ap.add_argument("--private")
    ap.add_argument("--denylist")
    a = ap.parse_args(argv)
    a.live = os.path.abspath(os.path.expanduser(a.live or "~/.claude"))
    a.overlay = a.overlay or os.path.join(a.live, "orch-live-overlay.json")
    a.private = a.private or os.path.join(a.live, "orch-export-private.json")
    a.denylist = a.denylist or os.path.join(a.live, "orch-export-denylist.txt")
    return a


def run(a, repo, tmp):
    check_config_files([a.overlay, a.private, a.denylist])
    refusals = []
    if a.rev is None:
        rev = "HEAD"
        if tree_dirty(repo):
            refusals.append("working tree not clean; commit, or pass --rev")
    else:
        rev = a.rev
    full, short = resolve_rev(repo, rev)
    overlay = load_overlay(a.overlay, repo)
    snap = os.path.join(tmp, "snapshot")
    os.mkdir(snap)
    snap_files = snapshot(repo, full, snap)
    ex = load_export(os.path.join(snap, "scripts", "export.py"))
    private = ex.load_private(a.private)
    pairs, moved = managed_set(ex, snap, private)
    guard_paths(pairs, moved, overlay["protect"], a.live)
    check_in_snapshot(pairs, snap_files)
    check_edit_paths(overlay["edits"], {r for _, r in pairs})
    stage = os.path.join(tmp, "stage")
    os.mkdir(stage)
    build_stage(ex, pairs, snap, snap_files, overlay["edits"], a.live, stage)
    ops = compute_ops(pairs, stage, a.live)
    check_timeout()
    baseline, base_refusals = baseline_checks(overlay["checks"], a.live, tmp)
    refusals += base_refusals
    refusals += run_gates(ex, pairs, snap, snap_files, overlay, a.private, a.denylist, stage, a.live,
                          baseline, tmp)
    return report(ops, refusals, short, stage, a.live)


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    if os.name != "posix":
        print("sync-live: POSIX only", file=sys.stderr); return 2
    if not a.dry_run:
        print("sync-live: only --dry-run is implemented yet", file=sys.stderr); return 2
    repo = REPO
    tmp = tempfile.mkdtemp(prefix="sync-live-")
    try:
        rt = os.path.realpath(tmp)
        for root in (repo, a.live):
            if under(rt, os.path.realpath(root)):
                raise bad("temp dir %s lies inside %s; set TMPDIR elsewhere" % (tmp, root))
        return run(a, repo, tmp)
    except Fail as f:
        print("sync-live: %s" % f.msg, file=sys.stderr)
        return f.code
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
