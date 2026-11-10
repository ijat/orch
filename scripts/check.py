#!/usr/bin/env python3
"""The one check command: everything that should pass before a commit is pushed.

  scripts/check.py [--only NAME,NAME,...] [--parity] [--list]

Checks (independent ones run in parallel; one line each, with its verdict and wall time):
  agents    scripts/build-agents.py plugin/skills/orchestrate/roles plugin/agents --check
  validate  `claude plugin validate plugin` and `claude plugin validate .`
  python    every test class in tests/python/test_*.py in its own process (own ORCH_DIR temp dir), up to the CPU
            count at a time; the totals are the sum over the classes
  public    scripts/check-public.py --tree . and --range HEAD (commits on no remote-tracking ref; none is a pass)
  parity    scripts/text-parity.py --base dce7cf7 (only with --parity: this task's gate)
Exit 0 only if every selected check passed. A check that cannot run (no `claude`, no denylist) fails and is never
skipped, and finding zero tests fails. Writes nothing to the repo or to ~/.claude.
"""
import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests", "python")
SCRIPTS = os.path.join(ROOT, "scripts")
PARITY_BASE = "dce7cf7"
# Test classes known to run long: started first so they overlap the rest (the run is about as long as the slowest).
SLOW_FIRST = ("test_merge", "test_usage_wait", "test_dashboard")

DISCOVER = r'''
import sys, unittest
sys.path.insert(0, %(tests)r)
def flat(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from flat(t)
        else:
            yield t
out = {}
for t in flat(unittest.TestLoader().discover(%(tests)r, pattern="test_*.py")):
    cls = type(t)
    if cls.__name__ == "_FailedTest":
        out.setdefault("!import:" + t.id(), 0)
        continue
    key = "%%s.%%s" %% (cls.__module__, cls.__name__)
    out[key] = out.get(key, 0) + 1
import json
print(json.dumps(out))
'''


def env_clean(**extra):
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"       # a check run leaves nothing behind in the repo
    for k in ("ORCH_DATA", "ORCH_DIR"):
        env.pop(k, None)
    env.update(extra)
    return env


def run(argv, cwd=ROOT, env=None, timeout=900):
    try:
        res = subprocess.run(argv, cwd=cwd, env=env or env_clean(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, errors="replace", timeout=timeout)
    except OSError as exc:
        return 127, "cannot run %s: %s" % (argv[0], exc.__class__.__name__)
    except subprocess.TimeoutExpired:
        return 124, "timed out after %ds" % timeout
    return res.returncode, res.stdout


def tail(text, n=15):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return "\n".join("    " + l for l in lines[-n:])


# -- the checks: each returns (ok, summary, detail) ----------------------------------------------------------------

def check_agents():
    argv = [sys.executable, os.path.join(SCRIPTS, "build-agents.py"), "plugin/skills/orchestrate/roles",
            "plugin/agents", "--check"]
    rc, out = run(argv)
    if rc == 0:
        return True, "agent files match their roles", ""
    return False, "agent files differ from their roles", (
        tail(out) + "\n    render with: python3 scripts/build-agents.py plugin/skills/orchestrate/roles plugin/agents")


def check_validate():
    if not shutil.which("claude"):
        return False, "`claude` is not on PATH: cannot validate", ""
    targets = ("plugin", ".")
    with concurrent.futures.ThreadPoolExecutor(len(targets)) as pool:
        results = list(pool.map(lambda t: run(["claude", "plugin", "validate", t]), targets))
    bad = [(t, out) for t, (rc, out) in zip(targets, results) if rc != 0]
    if not bad:
        return True, "plugin and marketplace manifests validate", ""
    return False, "validate failed for %s" % ", ".join(t for t, _ in bad), "\n".join(tail(o) for _, o in bad)


RAN_RE = re.compile(r"^Ran (\d+) tests? in", re.M)
SKIP_RE = re.compile(r"skipped=(\d+)")
FAIL_RE = re.compile(r"^(FAILED|OK)\b(.*)$", re.M)


def run_class(unit):
    tmp = tempfile.mkdtemp(prefix="orch-check-")
    try:
        rc, out = run([sys.executable, "-m", "unittest", unit], cwd=TESTS, env=env_clean(ORCH_DIR=tmp), timeout=900)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    ran = RAN_RE.search(out)
    skipped = SKIP_RE.search(out)
    return unit, rc, int(ran.group(1)) if ran else 0, int(skipped.group(1)) if skipped else 0, out


def check_python():
    rc, out = run([sys.executable, "-c", DISCOVER % {"tests": TESTS}], cwd=TESTS)
    try:
        found = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, "could not list the test classes", tail(out)
    broken = [k for k in found if k.startswith("!import:")]
    units = sorted((k for k in found if not k.startswith("!import:")), key=lambda k: (
        0 if k.split(".")[0] in SLOW_FIRST else 1, -found[k], k))
    if not units:
        return False, "found zero tests", ""
    workers = max(1, os.cpu_count() or 1)
    ran = skipped = 0
    failed = []
    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        for unit, rc, n, sk, text in pool.map(run_class, units):
            ran += n
            skipped += sk
            if rc != 0 or n != found[unit]:
                failed.append((unit, rc, n, found[unit], text))
    detail = ""
    for unit, rc, n, want, text in failed:
        detail += "  %s: exit %d, ran %d of %d\n%s\n" % (unit, rc, n, want, tail(text, 12))
    for k in broken:
        detail += "  %s: import error\n" % k[len("!import:"):]
    summary = "%d run, %d skipped, %d failed (%d classes, %d at a time)" % (
        ran, skipped, len(failed) + len(broken), len(units), workers)
    return (not failed and not broken), summary, detail.rstrip("\n")


def check_public():
    script = os.path.join(SCRIPTS, "check-public.py")
    notes = []
    for args in (["--tree", "."], ["--range", "HEAD"]):
        rc, out = run([sys.executable, script] + args)
        counts = [l for l in out.splitlines() if l.startswith("checked:") or l.startswith("denylist:")]
        if rc != 0:
            return False, "check-public %s exit %d" % (" ".join(args), rc), tail(out, 30)
        notes.append("%s: %s" % (args[0].lstrip("-"), counts[-1][len("checked: "):] if counts else "ok"))
    return True, "; ".join(notes), ""


def check_parity():
    script = os.path.join(SCRIPTS, "text-parity.py")
    if not os.path.isfile(script):
        return False, "scripts/text-parity.py does not exist yet", ""
    rc, out = run([sys.executable, script, "--base", PARITY_BASE])
    return rc == 0, "text parity against %s" % PARITY_BASE, "" if rc == 0 else tail(out, 30)


CHECKS = [("agents", check_agents), ("validate", check_validate), ("python", check_python),
          ("public", check_public)]
OPTIONAL = {"parity": check_parity}


def timed(name, fn):
    start = time.time()
    try:
        ok, summary, detail = fn()
    except Exception as exc:      # a check that blows up is a failed check, never a skip
        ok, summary, detail = False, "check crashed: %s: %s" % (exc.__class__.__name__, str(exc)[:200]), ""
    return name, ok, summary, detail, time.time() - start


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the repo's checks.")
    ap.add_argument("--only", help="comma-separated check names")
    ap.add_argument("--parity", action="store_true", help="also run text-parity (this task's gate)")
    ap.add_argument("--list", action="store_true", help="list the check names")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    table = dict(CHECKS)
    table.update(OPTIONAL)
    if ns.list:
        print("\n".join(table))
        return 0
    if ns.only:
        names = [n.strip() for n in ns.only.split(",") if n.strip()]
        unknown = [n for n in names if n not in table]
        if unknown or not names:
            sys.stderr.write("check: unknown check %s (known: %s)\n" % (", ".join(unknown) or "(none given)", ", ".join(table)))
            return 2
    else:
        names = [n for n, _ in CHECKS] + (["parity"] if ns.parity else [])
        if ns.parity and "parity" not in names:
            names.append("parity")
    start = time.time()
    failed = 0
    with concurrent.futures.ThreadPoolExecutor(len(names)) as pool:
        futures = [pool.submit(timed, n, table[n]) for n in names]
        for fut in concurrent.futures.as_completed(futures):
            name, ok, summary, detail, secs = fut.result()
            print("%s  %-8s %6.1fs  %s" % ("PASS" if ok else "FAIL", name, secs, summary))
            if detail:
                print(detail)
            sys.stdout.flush()
            failed += 0 if ok else 1
    print("%s  total %.1fs" % ("PASS" if not failed else "FAIL (%d)" % failed, time.time() - start))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
