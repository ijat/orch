#!/usr/bin/env python3
"""Write the live ~/.claude orch install from a commit of this repo plus a private overlay.

The repo is the source of truth; scripts/export.py maps a live install back to the repo's form, and
this script is its inverse. It builds the new live image in a temp stage (the commit's files, the
overlay's edits applied) and reports what would change.

Usage: scripts/sync-live.py [--dry-run] [--rev REV] [--init BASELINE_REV] [--allow-non-descendant]
                            [--live DIR] [--overlay FILE] [--private FILE] [--denylist FILE]
       scripts/sync-live.py --rollback [--dry-run] [--live DIR]

- --live      default ~/.claude (the live install)
- --overlay   default <live>/orch-live-overlay.json; owner-only (chmod 600), outside <repo>
- --private   default <live>/orch-export-private.json (the @@KEY@@ values export.py uses)
- --denylist  default <live>/orch-export-denylist.txt
- --rev       the commit to sync, default HEAD. With the default, a working tree that is not clean
              (tracked changes or untracked files) is a refusal, so what was tested is what is synced.
- --init      first sync only: BASELINE_REV is the commit the current live install was exported from.
              Its own export.py, run with `--src <live>`, must reproduce it exactly ("init proof");
              otherwise the run is refused. Refused when a manifest already exists.
- --allow-non-descendant  sync even when the last synced rev is not an ancestor of REV (a branch cut
              from older code would revert what the last sync wrote); the refusal becomes a NOTE.
- --dry-run   report only. Without it the run writes live (see "Write phase").
- --rollback  undo the newest sync record that is not `rolled-back` (see "Rollback"); with --rev, --init
              or --allow-non-descendant it is exit 2.

State (the write phase creates it; 0700): <live>/.orch-sync/
- manifest.json = {"version": 1, "rev", "tree", "synced_at", "record", "overlay_sha256",
  "files": {<live path>: {"sha256", "exec", "repo_path"}}}, the image the last sync wrote. Invalid JSON
  or a bad shape is exit 2. Without a manifest, a run needs --init.
- syncs/<stamp>/record.json, one per sync. A record left `pending` or `rollback-failed` is an
  interrupted sync and refuses every run ("run --rollback"), except a `pending` record whose stamp is
  the manifest's `record`: that sync completed (the manifest is written only after its checks pass),
  so it is only noted.

Classification, T = this rev's managed live paths, F = the manifest's (with --init: the baseline's,
"K", and no hash check):
- p in F whose live file is missing or differs (sha256, owner-exec) from the manifest: `drift`, refused;
- p in T and F: update or unchanged;
- p in T only: create if absent, `adopt` if identical to the new file (no write), else `conflict`
  (an unmanaged live file would be overwritten), refused;
- p in F only: `delete` (never with --init: there such a path is `left in place`, unless the tree
  filter takes it, which makes it an extra);
- a live file the tree filter takes that is in neither T nor F: `extra`, refused.

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

Output: `create`, `update`, `delete`, `adopt`, `conflict` and `left in place <path>` lines, a unified
diff per update and conflict (`--- live/<path>`, `+++ new/<path>`), `NOTE` lines, `REFUSE: <reason>`
lines, then `sync-live: dry run, rev <short>: C create, U update, D delete, A adopt, N unchanged,
R refusal(s)`, or `sync-live: dry run, rev <short>: nothing to do` (no op, no refusal, and a manifest
exists: an --init run always has the manifest to write).

Write phase (a run without --dry-run, after the same checks come out refusal-free; any refusal is
exit 1 and nothing, not even the state dir, is created under --live):
1. flock <state>/lock (non-blocking; busy is exit 1 "another sync or rollback is running"), held until
   exit; a `pending` record that the manifest names is marked `done`; drift, interrupted records and the
   op list are re-checked under the lock (a change is exit 1, nothing written);
2. record `syncs/<stamp>/record.json` (status `pending`): {"version": 1, "stamp", "kind": "init"|"sync",
   "status", "rev", "tree", "overlay_sha256", "prev_manifest": object|null, "ops": [{"path", "op",
   "repo_path", "before": {"sha256", "exec"}|null, "after": ...|null}], "dirs_created": []};
3. every update and delete target is copied to `syncs/<stamp>/before/<path>`, each verified against its
   before hash;
4. the ops are applied: create and update through a fresh inode (mkstemp in the target dir, fsync,
   fchmod, os.replace; never in place, never through a symlink), delete by os.remove, adopt writes
   nothing; each path is re-checked at its before state right before its write; new parent dirs are
   recorded in `dirs_created` before they are made;
5. post-gates on live: the written bytes, `orch-build-agents.py --check`, the overlay checks (against the
   baseline taken before the writes) and the round trip with `--src <live>`;
6. `manifest.json` is written (atomically, last), then the record is set to `done`.
Any failure or interrupt after the record (including Ctrl-C and SIGTERM) restores every applied op from
`before/` in reverse order (the same atomic write; a create is removed only while it is still at its
after state; a path at neither state is left alone and reported), removes the dirs it created, puts the
previous manifest back if this record wrote it, and sets the record to `rolled-back` (exit 1). If the
restore cannot be verified the record is `rollback-failed` (exit 3, the message names the record dir).
Backups and old records are never deleted.

Rollback (--rollback [--dry-run]): the target is the newest record whose status is not `rolled-back`
(none: exit 1 "nothing to roll back"). Refused (exit 1, the paths listed, nothing restored) unless
- a `done` record (or a `pending` one that the manifest names, a completed sync) has the manifest's
  `record` equal to its stamp and every op at its after state;
- a `pending` or `rollback-failed` record has every op at its before or after state;
and, for the ops still to undo, the backup in `before/` is present and matches its recorded hash.
Under the same lock, the same restore as the automatic one then runs (atomic writes, a create is
removed only at its after state, dirs the sync created go), manifest.json becomes the record's
`prev_manifest` (deleted when that is null) and the record `rolled-back`; exit 0, or 3 with the record
dir named if the restore cannot be verified. A `done` record is set to `rollback-failed` right before
the restore starts, so a rollback that is killed halfway is finished by running --rollback again.
Repeat --rollback to walk back through older records. --rollback --dry-run prints the ops and the
verdict and writes nothing, not even the lock. Only the ops of the record are touched, and the
record's paths must be relative, normalised and under skills/, agents/ or bin/. State, backups and
records are never deleted.

POSIX only. Exit 0 = would sync / synced; 1 = refused or rolled back, live is as before; 2 = bad input
or unsafe config; 3 = restore incomplete. A dry run creates, changes and deletes nothing under --live
or <repo>.
"""
import argparse, datetime, difflib, fcntl, hashlib, importlib.util, io, json, os, posixpath, re, shutil
import signal, stat, subprocess, sys, tarfile, tempfile, types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_DIRS = ("skills", "agents", "bin")
NO_EDIT_PREFIXES = ("agents/", "skills/orchestrate/roles/")
EXPORT_NAMES = ("FILES", "TREES", "MOVED", "tree_files", "fill", "load_private")
STATE_DIR = ".orch-sync"
RECORD_STATUSES = ("pending", "done", "rolled-back", "rollback-failed")
SHA1_OR_256 = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
SHA256 = re.compile(r"[0-9a-f]{64}")
MANIFEST_KEYS = {"version", "rev", "tree", "synced_at", "record", "overlay_sha256", "files"}


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


def git_rc(repo, *args):
    """(exit code, stdout text) of `git -C repo args`."""
    try:
        r = subprocess.run(["git", "-C", repo, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           env=git_env())
    except OSError as e:
        raise bad("git: %s" % e)
    return r.returncode, r.stdout.decode("utf-8", "replace")


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

def load_export(path, name="sync_live_export"):
    """The snapshot's export.py as a module, imported with bytecode off."""
    if not os.path.isfile(path):
        raise bad("snapshot has no scripts/export.py")
    old = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(name, path)
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

def build_stage(ex, pairs, snap, snap_files, edits, live, stage, skip=()):
    """Write the new image into `stage`; copy tree extras (live tree files that are not managed),
    except those in `skip` (the paths this sync deletes). Returns the extras copied."""
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
            if rel not in managed and rel not in skip:
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
    except BaseException:  # an interrupt (Ctrl-C, SIGTERM) must not leave a check running
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.wait()
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


# ---------- 7: state (manifest, records), classification, ancestry ----------

def read_json(path, what):
    """The JSON value at `path` (a regular file, not a symlink); exit 2 if unreadable or invalid."""
    if os.path.islink(path) or not os.path.isfile(path):
        raise bad("%s %s: not a regular file" % (what, path))
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise bad("%s %s: not valid JSON (%s)" % (what, path, e.__class__.__name__))


def check_manifest(m, path):
    if not isinstance(m, dict):
        raise bad("manifest %s: not a JSON object" % path)
    if m.get("version") != 1 or isinstance(m.get("version"), bool):
        raise bad("manifest %s: version must be 1" % path)
    if set(m) != MANIFEST_KEYS:
        raise bad("manifest %s: keys must be exactly %s" % (path, ", ".join(sorted(MANIFEST_KEYS))))
    if not isinstance(m["rev"], str) or not SHA1_OR_256.fullmatch(m["rev"]):
        raise bad("manifest %s: rev must be a full commit sha" % path)
    if not isinstance(m["record"], str) or not m["record"]:
        raise bad("manifest %s: record must be a non-empty string" % path)
    files = m["files"]
    if not isinstance(files, dict):
        raise bad("manifest %s: files must be an object" % path)
    for lr, e in files.items():
        if (not isinstance(e, dict) or set(e) != {"sha256", "exec", "repo_path"}
                or not isinstance(e["sha256"], str) or not SHA256.fullmatch(e["sha256"])
                or not isinstance(e["exec"], bool) or not isinstance(e["repo_path"], str)
                or not e["repo_path"]):
            raise bad("manifest %s: files[%r] must be {sha256, exec, repo_path}" % (path, lr))
    return m


def read_state(live):
    """(manifest dict or None, [(stamp, status)] sorted by stamp). Exit 2 on a malformed state."""
    state = os.path.join(live, STATE_DIR)
    if not os.path.lexists(state):
        return None, []
    if os.path.islink(state) or not os.path.isdir(state):
        raise bad("state dir %s: not a directory" % state)
    mpath = os.path.join(state, "manifest.json")
    manifest = check_manifest(read_json(mpath, "manifest"), mpath) if os.path.lexists(mpath) else None
    records = []
    syncs = os.path.join(state, "syncs")
    if os.path.lexists(syncs):
        if os.path.islink(syncs) or not os.path.isdir(syncs):
            raise bad("state dir %s: not a directory" % syncs)
        for stamp in sorted(os.listdir(syncs)):
            rpath = os.path.join(syncs, stamp, "record.json")
            if not os.path.lexists(rpath):
                continue  # the write phase writes the record before any live write: nothing happened
            r = read_json(rpath, "record")
            if (not isinstance(r, dict) or r.get("version") != 1 or isinstance(r.get("version"), bool)
                    or r.get("status") not in RECORD_STATUSES or r.get("stamp", stamp) != stamp):
                raise bad("record %s: needs version 1, its own stamp and a status of %s"
                          % (rpath, ", ".join(RECORD_STATUSES)))
            records.append((stamp, r["status"]))
    return manifest, records


def record_findings(records, manifest):
    """(notes, refusals) for records left pending or rollback-failed."""
    notes, refusals = [], []
    for stamp, status in records:
        if status == "pending" and manifest is not None and manifest["record"] == stamp:
            notes.append("completed sync %s was left pending; the next real run marks it done" % stamp)
        elif status in ("pending", "rollback-failed"):
            refusals.append("interrupted sync %s: run --rollback" % stamp)
    return notes, refusals


def tree_taken(ex, live):
    """Live paths that the tree filter takes under `live`."""
    out = []
    for t in ex.TREES:
        d = os.path.join(live, t)
        if os.path.isdir(d):
            out += [t + "/" + r for r in ex.tree_files(d)]
    return out


def classify(ex, pairs, stage, live, known, hashes):
    """(ops, refusals). ops = [(kind, live path)], kind in create, update, unchanged, adopt, conflict,
    delete, left (in place). `known` = F (manifest mode, `hashes` = its files) or K (init, `hashes`
    None: no drift check and nothing is deleted)."""
    ops, refusals = [], []
    T = [lr for lr, _ in pairs]
    tset = set(T)
    for lr in T:
        lp = os.path.join(live, lr)
        if not os.path.lexists(lp):
            ops.append(("create", lr)); continue
        if not os.path.isfile(lp):
            raise bad("live path %s exists but is not a regular file" % lp)
        same = sha_exec(lp) == sha_exec(os.path.join(stage, lr))
        if lr in known:
            ops.append(("unchanged" if same else "update", lr))
        elif same:
            ops.append(("adopt", lr))
        else:
            ops.append(("conflict", lr))
            refusals.append("conflict %s: unmanaged live file would be overwritten" % lr)
    taken = tree_taken(ex, live)
    if hashes is not None:
        for lr in sorted(known):
            lp = os.path.join(live, lr)
            if not os.path.lexists(lp):
                refusals.append("drift %s: missing since the last sync" % lr)
            elif not os.path.isfile(lp):
                raise bad("live path %s exists but is not a regular file" % lp)
            elif sha_exec(lp) != (hashes[lr]["sha256"], hashes[lr]["exec"]):
                refusals.append("drift %s: changed since the last sync" % lr)
        ops += [("delete", lr) for lr in sorted(set(known) - tset)]
    else:
        tk = set(taken)
        ops += [("left", lr) for lr in sorted(set(known) - tset) if lr not in tk]
    for lr in taken:
        if lr not in tset and (hashes is None or lr not in known):
            refusals.append("extra %s: a live tree file managed by neither this rev nor the last sync" % lr)
    return ops, refusals


def ancestry(repo, m, r, managed_repo, n_ops):
    """None if the last synced rev `m` is an ancestor of `r`, else the not-descendant message."""
    rc, _ = git_rc(repo, "merge-base", "--is-ancestor", m, r)
    if rc == 0:
        return None
    reverting = None
    if rc == 1:
        brc, base = git_rc(repo, "merge-base", m, r)
        c1, by_m = git_rc(repo, "diff", "--no-renames", "--name-only", base.strip(), m) if brc == 0 else (1, "")
        c2, m_r = git_rc(repo, "diff", "--no-renames", "--name-only", m, r)
        if brc == 0 and c1 == 0 and c2 == 0:
            reverting = sorted(set(by_m.splitlines()) & managed_repo & set(m_r.splitlines()))
    src, ms = git_rc(repo, "rev-parse", "--short", m + "^{commit}") if rc == 1 else (1, "")
    _, rs = git_rc(repo, "rev-parse", "--short", r)
    ms = ms.strip() if src == 0 else m[:7]
    if reverting is None:
        k, paths = "unknown", ""
    else:
        k = str(len(reverting))
        paths = ": " + ", ".join(reverting[:10]) + (", \u2026" if len(reverting) > 10 else "") if reverting else ""
    return ("not-descendant: last synced rev %s is not an ancestor of %s; %s managed file(s) changed by it "
            "would revert%s (%d file(s) differ from the last sync in total); pass --allow-non-descendant "
            "to sync anyway" % (ms, rs.strip(), k, paths, n_ops))


# ---------- 8: output ----------

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


def report(ops, notes, refusals, short, stage, live, has_manifest, dry=True):
    counts = {k: 0 for k in ("create", "update", "unchanged", "adopt", "conflict", "delete", "left")}
    for op, rel in ops:
        counts[op] += 1
        if op in ("create", "delete", "adopt"):
            print("%s %s" % (op, rel))
        elif op == "left":
            print("left in place %s" % rel)
        elif op in ("update", "conflict"):
            print("%s %s" % (op, rel))
            sys.stdout.write(diff_text(os.path.join(live, rel), os.path.join(stage, rel), rel))
    for n in notes:
        print("NOTE %s" % n)
    for r in refusals:
        print("REFUSE: %s" % r)
    work = counts["create"] + counts["update"] + counts["delete"] + counts["adopt"]
    label = "dry run, " if dry else ""
    if not refusals and not work and has_manifest:
        print("sync-live: %srev %s: nothing to do" % (label, short))
    elif dry or refusals or not work:
        print("sync-live: %srev %s: %d create, %d update, %d delete, %d adopt, %d unchanged, "
              "%d refusal(s)" % (label, short, counts["create"], counts["update"], counts["delete"],
                                 counts["adopt"], counts["unchanged"], len(refusals)))
    return 0 if not refusals else 1


# ---------- 9: write phase (lock, record, backups, atomic writes, post-gates, restore, manifest) ----------

WRITE_KINDS = ("create", "update", "delete", "adopt")


class Interrupted(BaseException):
    """SIGTERM or SIGHUP during the write phase: handled like Ctrl-C (restore, then exit)."""


def _raise_interrupted(signum, frame):
    raise Interrupted(signal.Signals(signum).name)


def utc_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tup(d):
    """(sha256, exec) of a record's {"sha256", "exec"} state; None for an absent file."""
    return None if d is None else (d["sha256"], d["exec"])


def sdict(t):
    return None if t is None else {"sha256": t[0], "exec": t[1]}


def state_of(path):
    """None if `path` is absent, (sha256, owner-exec) if it is a regular file, else a marker tuple that
    equals no recorded state. Never follows a symlink and never blocks on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        return ("unreadable",)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            return ("special",)
        data = f.read()
    return hashlib.sha256(data).hexdigest(), bool(st.st_mode & stat.S_IXUSR)


def fsync_dir(d):
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path, data, mode):
    """Replace `path` with `data` through a fresh inode: mkstemp in the same dir (O_EXCL, random name),
    write, fsync, fchmod, os.replace. Never writes the existing inode, never follows a symlink."""
    d, base = os.path.split(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + base + ".sync-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
            os.fchmod(f.fileno(), mode)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.remove(tmp)
    fsync_dir(d)


def write_json_atomic(path, obj):
    atomic_write(path, (json.dumps(obj, indent=1, sort_keys=True) + "\n").encode("utf-8"), 0o600)


def exec_mode(exe):
    return 0o755 if exe else 0o644


def check_no_symlink(live, rel):
    cur = live
    for part in rel.split("/"):
        cur = os.path.join(cur, part)
        if os.path.islink(cur):
            raise Fail(1, "%s: %s is a symlink; nothing was written there" % (rel, cur))


def ensure_parents(live, rel, record, rec_path):
    """Create the missing parent dirs of `rel` top-down, recording each in the record BEFORE making it."""
    parts = rel.split("/")[:-1]
    for i in range(1, len(parts) + 1):
        d = "/".join(parts[:i])
        full = os.path.join(live, d)
        if os.path.lexists(full):
            continue
        record["dirs_created"].append(d)
        write_json_atomic(rec_path, record)
        try:
            os.mkdir(full, 0o755)
        except FileExistsError:
            pass


def restore_file(rec_dir, live, op):
    """Put the backed-up bytes and exec bit of an update/delete op back, atomically. Problem text or None."""
    bp = os.path.join(rec_dir, "before", op["path"])
    try:
        with open(bp, "rb") as f:
            data = f.read()
    except OSError as e:
        return "%s: backup unreadable (%s)" % (op["path"], e.__class__.__name__)
    if hashlib.sha256(data).hexdigest() != op["before"]["sha256"]:
        return "%s: backup does not match its recorded hash" % op["path"]
    try:
        atomic_write(os.path.join(live, op["path"]), data, exec_mode(op["before"]["exec"]))
    except OSError as e:
        return "%s: restore failed (%s)" % (op["path"], e)
    return None


def restore(live, rec_dir, record):
    """Undo a record's ops, newest first. The one restore for the automatic undo and --rollback.
    A path at its before state is skipped (never applied or already restored); a path at neither its
    before nor its after state is left alone and reported. Empty dirs the sync created are removed.
    Returns the list of problems; empty means every op is verified at its before state."""
    problems = {}
    for op in reversed(record["ops"]):
        rel = op["path"]
        p = os.path.join(live, rel)
        cur = state_of(p)
        if cur == tup(op["before"]):
            continue
        if cur != tup(op["after"]):
            problems[rel] = "%s: at neither its before nor its after state; left alone" % rel
            continue
        try:
            if op["op"] == "create":
                os.remove(p)
            else:
                msg = restore_file(rec_dir, live, op)
                if msg:
                    problems[rel] = msg
        except OSError as e:
            problems[rel] = "%s: restore failed (%s)" % (rel, e)
    for d in reversed(record["dirs_created"]):
        try:
            os.rmdir(os.path.join(live, d))
        except OSError:
            pass
    for op in record["ops"]:
        if op["path"] not in problems and state_of(os.path.join(live, op["path"])) != tup(op["before"]):
            problems[op["path"]] = "%s: not at its pre-sync state after the restore" % op["path"]
    return list(problems.values())


def revert_manifest(state, record):
    """Put the previous manifest back if manifest.json is the one this record wrote. Problems list."""
    mp = os.path.join(state, "manifest.json")
    try:
        cur = read_json(mp, "manifest") if os.path.lexists(mp) else None
    except Fail as f:
        return ["manifest: %s" % f.msg]
    if not isinstance(cur, dict) or cur.get("record") != record["stamp"]:
        return []
    try:
        if record["prev_manifest"] is None:
            os.remove(mp)
        else:
            write_json_atomic(mp, record["prev_manifest"])
    except OSError as e:
        return ["manifest: could not restore the previous one (%s)" % e]
    return []


def ensure_state_dir(state):
    if not os.path.lexists(state):
        os.mkdir(state, 0o700)
        os.chmod(state, 0o700)
    elif os.path.islink(state) or not os.path.isdir(state):
        raise bad("state dir %s: not a directory" % state)
    elif os.stat(state).st_uid != os.getuid():
        raise bad("state dir %s: not owned by the current user" % state)


def take_lock(state):
    """Exclusive non-blocking flock on <state>/lock; the fd (kept open until exit) or exit 1."""
    lp = os.path.join(state, "lock")
    try:
        fd = os.open(lp, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        raise bad("cannot open the lock %s: %s" % (lp, e))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise Fail(1, "another sync or rollback is running")
    return fd


def new_stamp_dir(state):
    syncs = os.path.join(state, "syncs")
    if not os.path.lexists(syncs):
        os.mkdir(syncs, 0o700)
    base = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n = 0
    while True:
        stamp = base if n == 0 else "%s-%d" % (base, n)
        d = os.path.join(state, "syncs", stamp)
        try:
            os.mkdir(d, 0o700)
        except FileExistsError:
            n += 1
            continue
        return stamp, d


def finish_completed_pending(state, manifest, records):
    """A `pending` record whose stamp is the manifest's finished (the manifest is written last): mark
    it done, atomically."""
    for stamp, status in records:
        if status == "pending" and manifest is not None and manifest["record"] == stamp:
            rp = os.path.join(state, "syncs", stamp, "record.json")
            rec = read_json(rp, "record")
            rec["status"] = "done"
            write_json_atomic(rp, rec)


def describe(e):
    if isinstance(e, Fail):
        return e.msg
    if isinstance(e, (KeyboardInterrupt, Interrupted)):
        return "interrupted (%s)" % (e if isinstance(e, Interrupted) else "Ctrl-C")
    return "%s: %s" % (e.__class__.__name__, e)


def post_gates(c):
    """The gates again, on live, after the writes: written bytes, rendered agents, overlay checks (with
    the baseline taken before the writes) and the round trip with --src live."""
    refusals = []
    for o in c.wops:
        if o["after"] is not None and state_of(os.path.join(c.live, o["path"])) != tup(o["after"]):
            refusals.append("write: %s does not hold the staged content" % o["path"])
    refusals += gate_agents(c.snap, c.live, c.tmp)
    refusals += gate_checks(c.overlay["checks"], c.live, c.live, c.baseline, c.tmp)
    refusals += gate_roundtrip(c.snap, c.snap_files, c.live, c.private_path, c.deny_path, c.tmp)
    return refusals


def abort_sync(c, state, rec_dir, record, e):
    """Restore everything from the backups after a failure, set the record, return the exit code."""
    saved = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        print("sync-live: sync failed: %s" % describe(e))
        problems = restore(c.live, rec_dir, record) + revert_manifest(state, record)
        record["status"] = "rolled-back" if not problems else "rollback-failed"
        try:
            write_json_atomic(os.path.join(rec_dir, "record.json"), record)
        except OSError as werr:
            record["status"] = "rollback-failed"
            problems.append("record not updated (%s)" % werr)
        if problems:
            print("sync-live: RESTORE INCOMPLETE, record %s is rollback-failed:" % record["stamp"])
            for pr in problems:
                print("  %s" % pr)
            print("sync-live: record dir: %s (backups are in its before/ dir)" % rec_dir)
            return 3
        print("sync-live: restored the live install from the backups (record %s rolled-back)"
              % record["stamp"])
        return 1
    finally:
        for s, h in saved.items():
            signal.signal(s, h)


def locked_sync(c, state):
    """Everything after the lock is held. Returns the exit code."""
    live = c.live
    manifest, records = read_state(live)
    finish_completed_pending(state, manifest, records)
    manifest, records = read_state(live)
    notes, refusals = record_findings(records, manifest)
    ops, class_refusals = classify(c.ex, c.pairs, c.stage, live, c.known, c.hashes)
    if refusals or class_refusals or ops != c.ops or manifest != c.manifest:
        raise Fail(1, "live changed while the sync was starting; nothing was written; run again")
    wops = [(k, lr) for k, lr in ops if k in WRITE_KINDS]
    if not wops and c.init is None:
        return 0
    stamp, rec_dir = new_stamp_dir(state)
    rec_path = os.path.join(rec_dir, "record.json")
    c.wops = []
    for kind, lr in wops:
        c.wops.append({"path": lr, "op": kind, "repo_path": c.repo_of[lr],
                       "before": None if kind == "create" else sdict(state_of(os.path.join(live, lr))),
                       "after": None if kind == "delete" else sdict(sha_exec(os.path.join(c.stage, lr)))})
    record = {"version": 1, "stamp": stamp, "kind": "init" if c.init is not None else "sync",
              "status": "pending", "rev": c.full, "tree": c.tree, "overlay_sha256": c.overlay_sha,
              "prev_manifest": manifest, "ops": c.wops, "dirs_created": []}
    old = {s: signal.signal(s, _raise_interrupted) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        try:
            write_json_atomic(rec_path, record)
            for o in c.wops:  # back up everything first, each verified against its before hash
                if o["op"] in ("update", "delete"):
                    with open(os.path.join(live, o["path"]), "rb") as f:
                        data = f.read()
                    if (hashlib.sha256(data).hexdigest() != o["before"]["sha256"]
                            or state_of(os.path.join(live, o["path"])) != tup(o["before"])):
                        raise Fail(1, "%s changed while it was being backed up; nothing was written"
                                   % o["path"])
                    bp = os.path.join(rec_dir, "before", o["path"])
                    os.makedirs(os.path.dirname(bp), exist_ok=True)
                    atomic_write(bp, data, exec_mode(o["before"]["exec"]))
            for o in c.wops:
                p = os.path.join(live, o["path"])
                check_no_symlink(live, o["path"])
                if state_of(p) != tup(o["before"]):
                    raise Fail(1, "%s changed during the sync" % o["path"])
                if o["op"] in ("create", "update"):
                    ensure_parents(live, o["path"], record, rec_path)
                    with open(os.path.join(c.stage, o["path"]), "rb") as f:
                        data = f.read()
                    atomic_write(p, data, exec_mode(o["after"]["exec"]))
                elif o["op"] == "delete":
                    os.remove(p)
            failures = post_gates(c)
            if failures:
                raise Fail(1, "post-sync gates failed:\n  " + "\n  ".join(failures))
            write_json_atomic(os.path.join(state, "manifest.json"),
                              {"version": 1, "rev": c.full, "tree": c.tree, "synced_at": utc_iso(),
                               "record": stamp, "overlay_sha256": c.overlay_sha, "files": c.new_files})
            record["status"] = "done"
            write_json_atomic(rec_path, record)
        except BaseException as e:
            return abort_sync(c, state, rec_dir, record, e)
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    n = {k: 0 for k in WRITE_KINDS}
    for o in c.wops:
        n[o["op"]] += 1
    unchanged = sum(1 for k, _ in c.ops if k == "unchanged")
    print("sync-live: synced rev %s: %d created, %d updated, %d deleted, %d adopted, %d unchanged "
          "(record %s)" % (c.short, n["create"], n["update"], n["delete"], n["adopt"], unchanged, stamp))
    return 0


def write_phase(c):
    """The real sync, after the plan is final and refusal-free: lock, then locked_sync."""
    state = os.path.join(c.live, STATE_DIR)
    work = any(k in WRITE_KINDS for k, _ in c.ops)
    pending = any(st == "pending" for _, st in c.records)
    if not work and not pending and c.init is None:
        return 0
    ensure_state_dir(state)
    fd = take_lock(state)
    try:
        return locked_sync(c, state)
    finally:
        os.close(fd)


# ---------- 10: rollback ----------

def safe_rel(p):
    """True for a relative, normalised path under TOP_DIRS with no dot component."""
    if not isinstance(p, str) or not p or "\0" in p or p.startswith("/") or posixpath.normpath(p) != p:
        return False
    parts = p.split("/")
    return parts[0] in TOP_DIRS and not any(x == ".." or x.startswith(".") for x in parts)


def state_ok(d):
    return d is None or (isinstance(d, dict) and set(d) == {"sha256", "exec"}
                         and isinstance(d["sha256"], str) and bool(SHA256.fullmatch(d["sha256"]))
                         and isinstance(d["exec"], bool))


def load_record(state, stamp):
    """The full record `syncs/<stamp>/record.json`, its shape checked (exit 2 otherwise): the restore
    removes and rewrites live paths from it."""
    rpath = os.path.join(state, "syncs", stamp, "record.json")
    r = read_json(rpath, "record")

    def malformed(why):
        return bad("record %s: %s" % (rpath, why))
    ops, dirs = r.get("ops"), r.get("dirs_created", [])
    if not isinstance(ops, list) or not isinstance(dirs, list) or r.get("stamp") != stamp:
        raise malformed("needs its own stamp, an ops list and a dirs_created list")
    if r.get("prev_manifest") is not None:
        check_manifest(r["prev_manifest"], rpath + " prev_manifest")
    for d in dirs:
        if not safe_rel(d):
            raise malformed("unsafe dirs_created entry %r" % (d,))
    seen = set()
    for o in ops:
        if (not isinstance(o, dict) or not safe_rel(o.get("path")) or o["path"] in seen
                or o.get("op") not in WRITE_KINDS or not state_ok(o.get("before"))
                or not state_ok(o.get("after"))):
            raise malformed("bad op entry %r" % (o,))
        seen.add(o["path"])
        want = {"create": (False, True), "delete": (True, False)}.get(o["op"], (True, True))
        if (o["before"] is not None, o["after"] is not None) != want:
            raise malformed("op %s %s has the wrong before/after states" % (o["op"], o["path"]))
        if o["op"] == "adopt" and o["before"] != o["after"]:
            raise malformed("adopt %s must have equal before/after states" % o["path"])
    return r


def plan_rollback(live):
    """(stamp, rec_dir, record, effective status, op lines, refusals) for the newest record that is not
    rolled-back, or None. Reads only."""
    state = os.path.join(live, STATE_DIR)
    manifest, records = read_state(live)
    todo = [(s, st) for s, st in records if st != "rolled-back"]
    if not todo:
        return None
    stamp, status = todo[-1]
    if status == "pending" and manifest is not None and manifest["record"] == stamp:
        status = "done"  # the sync completed; only the status write was lost
    rec_dir = os.path.join(state, "syncs", stamp)
    record = load_record(state, stamp)
    refusals, lines = [], []
    if status == "done" and (manifest is None or manifest["record"] != stamp):
        refusals.append("the manifest was written by another sync (record %s), not by %s"
                        % (manifest["record"] if manifest else "none", stamp))
    for o in record["ops"]:
        rel = o["path"]
        p = os.path.join(live, rel)
        cur = state_of(p)
        try:
            check_no_symlink(live, rel)
        except Fail as f:
            refusals.append(f.msg)
            continue
        at_before, at_after = cur == tup(o["before"]), cur == tup(o["after"])
        if status == "done" and not at_after:
            refusals.append("%s: changed since the sync; not at its synced state" % rel)
        elif not at_before and not at_after:
            refusals.append("%s: at neither its before nor its after state" % rel)
        elif o["op"] in ("update", "delete") and not at_before:
            bp = os.path.join(rec_dir, "before", rel)
            try:
                with open(bp, "rb") as f:
                    ok = hashlib.sha256(f.read()).hexdigest() == o["before"]["sha256"]
            except OSError:
                ok = False
            if not ok:
                refusals.append("%s: backup in %s is missing or does not match its recorded hash"
                                % (rel, os.path.join(rec_dir, "before")))
        lines.append("undo %s %s%s" % (o["op"], rel, " (already at its pre-sync state)"
                                      if at_before and o["op"] != "adopt" else ""))
    for d in record["dirs_created"]:
        try:
            check_no_symlink(live, d)
        except Fail as f:
            refusals.append(f.msg)
    return stamp, rec_dir, record, status, lines, refusals


def print_plan(plan, label):
    stamp, _, record, status, lines, refusals = plan
    print("%s record %s (%s, %s, rev %s)" % (label, stamp, record.get("kind", "?"), status,
                                              str(record.get("rev", "?"))[:12]))
    for ln in lines:
        print(ln)
    for r in refusals:
        print("REFUSE: %s" % r)


def rollback(a):
    live = a.live
    if a.rev is not None or a.init is not None or a.allow_non_descendant:
        raise bad("--rollback cannot be combined with --rev, --init or --allow-non-descendant")
    state = os.path.join(live, STATE_DIR)
    plan = plan_rollback(live)
    if plan is None:
        print("sync-live: nothing to roll back")
        return 1
    if a.dry_run:
        print_plan(plan, "sync-live: rollback dry run,")
        print("sync-live: rollback dry run: %s" % ("%d refusal(s)" % len(plan[5]) if plan[5]
                                                     else "would roll back record %s" % plan[0]))
        return 1 if plan[5] else 0
    ensure_state_dir(state)
    fd = take_lock(state)
    try:
        plan = plan_rollback(live)  # again, now that no other sync or rollback can run
        if plan is None:
            print("sync-live: nothing to roll back")
            return 1
        print_plan(plan, "sync-live: rolling back")
        if plan[5]:
            print("sync-live: rollback refused; nothing was restored")
            return 1
        return do_rollback(live, state, plan)
    finally:
        os.close(fd)


def do_rollback(live, state, plan):
    stamp, rec_dir, record, status, _, _ = plan
    rec_path = os.path.join(rec_dir, "record.json")
    saved = {s: signal.signal(s, signal.SIG_IGN) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        problems = []
        try:
            if status != "rollback-failed":
                # in progress: a rollback killed halfway leaves ops at before OR after, which is exactly
                # what a rollback-failed record is allowed to have
                record["status"] = "rollback-failed"
                write_json_atomic(rec_path, record)
            problems = restore(live, rec_dir, record) + revert_manifest(state, record)
            record["status"] = "rolled-back" if not problems else "rollback-failed"
            write_json_atomic(rec_path, record)
        except OSError as e:
            record["status"] = "rollback-failed"
            problems.append("record or state not updated (%s)" % e)
        if problems:
            print("sync-live: RESTORE INCOMPLETE, record %s is rollback-failed:" % stamp)
            for pr in problems:
                print("  %s" % pr)
            print("sync-live: record dir: %s (backups are in its before/ dir)" % rec_dir)
            return 3
        print("sync-live: rolled back record %s: the live install is as before that sync" % stamp)
        return 0
    finally:
        for s, h in saved.items():
            signal.signal(s, h)


# ---------- main ----------

def parse_args(argv):
    ap = argparse.ArgumentParser(description="Sync the live orch install from this repo.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rev")
    ap.add_argument("--init", metavar="BASELINE_REV")
    ap.add_argument("--allow-non-descendant", action="store_true")
    ap.add_argument("--rollback", action="store_true")
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


def init_known(a, repo, tmp, private, protect):
    """(K = the baseline's managed live paths, init-proof refusals) for --init BASELINE_REV."""
    bfull, _ = resolve_rev(repo, a.init)
    bsnap = os.path.join(tmp, "baseline")
    os.mkdir(bsnap)
    bfiles = snapshot(repo, bfull, bsnap)
    bex = load_export(os.path.join(bsnap, "scripts", "export.py"), "sync_live_export_baseline")
    bpairs, bmoved = managed_set(bex, bsnap, private)
    guard_paths(bpairs, bmoved, protect, a.live)
    check_in_snapshot(bpairs, bfiles)
    proof = gate_roundtrip(bsnap, bfiles, a.live, a.private, a.denylist, tmp)
    return {lr for lr, _ in bpairs}, ["init proof failed: %s" % r for r in proof]


def run(a, repo, tmp):
    check_config_files([a.overlay, a.private, a.denylist])
    manifest, records = read_state(a.live)
    notes, refusals = record_findings(records, manifest)
    if manifest is None and a.init is None:
        refusals.append("first sync: pass --init <rev>")
    if manifest is not None and a.init is not None:
        refusals.append("--init: a manifest already exists (%s)" % os.path.join(a.live, STATE_DIR))
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
    tset = {lr for lr, _ in pairs}
    if a.init is not None:
        known, proof = init_known(a, repo, tmp, private, overlay["protect"])
        refusals += proof
        hashes, deletes = None, set()
    else:
        hashes = manifest["files"] if manifest is not None else {}
        known, deletes = set(hashes), set(hashes) - tset
        # a delete target is a live path too: the same guards apply before anything touches it
        guard_paths([(lr, hashes[lr]["repo_path"]) for lr in sorted(deletes)], moved, overlay["protect"],
                    a.live)
    stage = os.path.join(tmp, "stage")
    os.mkdir(stage)
    build_stage(ex, pairs, snap, snap_files, overlay["edits"], a.live, stage, skip=deletes)
    ops, class_refusals = classify(ex, pairs, stage, a.live, known, hashes)
    refusals += class_refusals
    if manifest is not None and a.init is None:
        managed_repo = {r for _, r in pairs} | {e["repo_path"] for e in hashes.values()}
        n = sum(1 for op, _ in ops if op in ("create", "update", "delete"))
        msg = ancestry(repo, manifest["rev"], full, managed_repo, n)
        if msg is not None:
            (notes if a.allow_non_descendant else refusals).append(msg)
    check_timeout()
    baseline, base_refusals = baseline_checks(overlay["checks"], a.live, tmp)
    refusals += base_refusals
    refusals += run_gates(ex, pairs, snap, snap_files, overlay, a.private, a.denylist, stage, a.live,
                          baseline, tmp)
    rc = report(ops, notes, refusals, short, stage, a.live, manifest is not None, dry=a.dry_run)
    if a.dry_run or rc:
        return rc
    tree = git(repo, "rev-parse", "--verify", "--quiet", full + "^{tree}")
    with open(a.overlay, "rb") as f:
        overlay_sha = hashlib.sha256(f.read()).hexdigest()
    new_files = {}
    for lr, rr in pairs:
        sha, exe = sha_exec(os.path.join(stage, lr))
        new_files[lr] = {"sha256": sha, "exec": exe, "repo_path": rr}
    ctx = types.SimpleNamespace(
        live=a.live, ex=ex, pairs=pairs, stage=stage, known=known, hashes=hashes, ops=ops,
        manifest=manifest, records=records, init=a.init, full=full, short=short,
        tree=tree.decode().strip() if tree else "", overlay_sha=overlay_sha, overlay=overlay,
        new_files=new_files, repo_of=dict(pairs, **{lr: h["repo_path"] for lr, h in (hashes or {}).items()}),
        snap=snap, snap_files=snap_files, baseline=baseline,
        private_path=a.private, deny_path=a.denylist, tmp=tmp)
    return write_phase(ctx)


def main(argv=None):
    a = parse_args(sys.argv[1:] if argv is None else argv)
    if os.name != "posix":
        print("sync-live: POSIX only", file=sys.stderr); return 2
    repo = REPO
    try:
        if a.rollback:
            return rollback(a)
        tmp = tempfile.mkdtemp(prefix="sync-live-")
        try:
            rt = os.path.realpath(tmp)
            for root in (repo, a.live):
                if under(rt, os.path.realpath(root)):
                    raise bad("temp dir %s lies inside %s; set TMPDIR elsewhere" % (tmp, root))
            return run(a, repo, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except Fail as f:
        print("sync-live: %s" % f.msg, file=sys.stderr)
        return f.code


if __name__ == "__main__":
    sys.exit(main())
