#!/usr/bin/env python3
"""Public-safety gate: nothing in what is published may match the private denylist.

  check-public.py [--tree DIR | --rev REV] [--range A..B | --range B] [--denylist FILE] [--show]

  --tree DIR   the files git would commit from DIR (`git ls-files -co --exclude-standard`, read from disk), paths
               and contents. Ignored private files (CLAUDE.local.md) are not scanned.
  --rev REV    one commit's whole tree, paths and contents.
  --range      every commit in the range, merges included: its message (`git log -1 --format=%B`; author, committer
               and date lines are never scanned), and for every path it adds or changes against its first parent the
               path and the whole blob (so binary files and symlink targets are covered). `--range A..B` is
               `git rev-list A..B`; `--range B` is the commits of B that are on no remote-tracking ref.
  --denylist   one regular expression per line; blank lines and lines starting with `#` are skipped. Without it, the
               private data folder's orch-export-denylist.txt is used, or the legacy ~/.claude one while the new one
               is missing (printed as `denylist: private` or `denylist: legacy`). An explicit file never falls back.
  --show       print the matching line as well; by default hits give `path:line: pattern #k` only, never the text.

Hits: `[<commit12>:]<path>:<line>: pattern #k`; a path hit is `<path>:path`, a message hit `<commit12>:message:<line>`.
Exit 0 clean, 1 hits, 2 bad input or any git/file error (nothing checked is never a pass). LICENSE is exempt.
Matching is export.py's: one case-insensitive alternation over each line (UTF-8, errors=replace).
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "plugin", "bin"))
from orchlib import paths  # noqa: E402

ZERO_MODE_GITLINK = "160000"
LINE_SPLIT = re.compile(r"\r\n|\r|\n")


class Fail(Exception):
    """Exit 2: bad input, or something that could not be read (never a skip)."""


def run_git(cwd, *args):
    try:
        res = subprocess.run(["git", "-C", cwd] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise Fail("cannot run git: %s" % exc)
    if res.returncode != 0:
        raise Fail("git %s failed: %s" % (" ".join(args[:2]), res.stderr.decode("utf-8", "replace").strip()[:200]))
    return res.stdout


def clean(text):
    """A path for display: control characters escaped, so a hostile name can't forge output lines."""
    return re.sub(r"[\x00-\x1f\x7f]", lambda m: "\\x%02x" % ord(m.group(0)), text)


class Denylist(object):
    def __init__(self, patterns):
        self.patterns = patterns
        try:
            self.each = [re.compile(p, re.I) for p in patterns]
            self.rx = re.compile("|".join("(?:%s)" % p for p in patterns), re.I)
        except re.error as exc:
            raise Fail("bad pattern in the denylist: %s" % exc)

    def which(self, line):
        """The 1-based number of the first pattern that matches the line, or None."""
        if not self.rx.search(line):
            return None
        for k, rx in enumerate(self.each, 1):
            if rx.search(line):
                return k
        return None  # unreachable: the alternation matched, so one member does

    def scan_text(self, text):
        hits = []
        for i, line in enumerate(LINE_SPLIT.split(text), 1):
            k = self.which(line)
            if k:
                hits.append((i, k, line))
        return hits


def load_denylist(explicit):
    if explicit:
        path, used = explicit, None
    else:
        path, used = paths.denylist_path(), "private"
        if not os.path.lexists(path):
            path, used = os.path.join(os.path.expanduser("~"), ".claude", "orch-export-denylist.txt"), "legacy"
    if not os.path.isfile(path):
        raise Fail("no denylist at %s: nothing checked (not a pass)" % (path if explicit else used))
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise Fail("cannot read the denylist (%s): %s" % (used or "explicit", exc.__class__.__name__))
    patterns = [l.strip() for l in lines if l.strip() and not l.startswith("#")]  # export.py:238-239
    if not patterns:
        raise Fail("the denylist holds no patterns: nothing checked (not a pass)")
    if used:
        print("denylist: %s" % used)
    return Denylist(patterns)


class Blobs(object):
    """Blob contents through one `git cat-file --batch` process."""

    def __init__(self, cwd):
        try:
            self.proc = subprocess.Popen(["git", "-C", cwd, "cat-file", "--batch"], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError as exc:
            raise Fail("cannot run git: %s" % exc)

    def read(self, sha):
        try:
            self.proc.stdin.write(sha.encode("ascii") + b"\n")
            self.proc.stdin.flush()
            head = self.proc.stdout.readline().split()
            if len(head) != 3 or head[1] != b"blob":
                raise Fail("unreadable blob %s" % sha[:12])
            size = int(head[2])
            data = self.proc.stdout.read(size)
            self.proc.stdout.read(1)  # the newline after the content
        except (OSError, ValueError):
            raise Fail("unreadable blob %s" % sha[:12])
        if len(data) != size:
            raise Fail("short read of blob %s" % sha[:12])
        return data

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, Exception):
            self.proc.kill()


class Scan(object):
    def __init__(self, deny, cwd, show):
        self.deny, self.cwd, self.show = deny, cwd, show
        self.hits = 0
        self.files = 0
        self.commits = 0
        self.blobs = 0
        self._cache = {}
        self._blobs = None

    def blobs_reader(self):
        if self._blobs is None:
            self._blobs = Blobs(self.cwd)
        return self._blobs

    def report(self, prefix, where, line_no, k, text):
        self.hits += 1
        out = "%s%s:%s: pattern #%d" % (prefix, where, line_no, k)
        if self.show:
            out += ": " + text.strip()[:120]
        print(out)

    def check_path(self, prefix, path):
        k = self.deny.which(path)
        if k:
            self.report(prefix, clean(path), "path", k, path)

    def check_content(self, prefix, path, data):
        """Scan the bytes of one file (a blob or a file on disk) for `path`, once per distinct content."""
        self.files += 1
        key = hashlib.sha1(data).digest()
        if key not in self._cache:
            self.blobs += 1
            self._cache[key] = self.deny.scan_text(data.decode("utf-8", errors="replace"))
        for line_no, k, line in self._cache[key]:
            self.report(prefix, clean(path), line_no, k, line)

    def check_blob(self, prefix, path, sha):
        key = ("blob", sha)
        self.files += 1
        if key not in self._cache:
            self.blobs += 1
            data = self.blobs_reader().read(sha)
            self._cache[key] = self.deny.scan_text(data.decode("utf-8", errors="replace"))
        for line_no, k, line in self._cache[key]:
            self.report(prefix, clean(path), line_no, k, line)

    def tree(self, top):
        raw = run_git(top, "ls-files", "-z", "-co", "--exclude-standard", "--deduplicate")
        for rel in sorted(set(p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p)):
            if rel == "LICENSE":
                continue
            full = os.path.join(top, rel)
            if not os.path.lexists(full):
                continue  # in the index, deleted on disk: git would not commit it
            if os.path.isdir(full) and not os.path.islink(full):
                continue  # a submodule or nested checkout
            self.check_path("", rel)
            try:
                if os.path.islink(full):
                    data = os.readlink(full).encode("utf-8", "surrogateescape")
                else:
                    with open(full, "rb") as fh:
                        data = fh.read()
            except OSError as exc:
                raise Fail("cannot read %s: %s" % (clean(rel), exc.__class__.__name__))
            self.check_content("", rel, data)

    def rev(self, rev):
        out = run_git(self.cwd, "ls-tree", "-r", "-z", "--full-tree", rev)
        for entry in out.split(b"\0"):
            if not entry:
                continue
            meta, _, path = entry.partition(b"\t")
            mode, _, sha = meta.decode("ascii", "replace").split(" ")[:3]
            path = path.decode("utf-8", "surrogateescape")
            if mode == ZERO_MODE_GITLINK or path == "LICENSE":
                continue
            self.check_path("", path)
            self.check_blob("", path, sha)

    def commit_list(self, spec):
        """[(sha, first parent or None)] for the range."""
        if ".." in spec:
            out = run_git(self.cwd, "rev-list", "--parents", spec)
        else:
            out = run_git(self.cwd, "rev-list", "--parents", spec, "--not", "--remotes")
        commits = []
        for line in out.decode("ascii", "replace").splitlines():
            parts = line.split()
            if parts:
                commits.append((parts[0], parts[1] if len(parts) > 1 else None))
        return commits

    def range(self, spec):
        for sha, parent in self.commit_list(spec):
            self.commits += 1
            prefix = sha[:12] + ":"
            message = run_git(self.cwd, "log", "-1", "--format=%B", sha).decode("utf-8", errors="replace")
            for line_no, k, line in self.deny.scan_text(message):
                self.report(prefix, "message", line_no, k, line)
            if parent:
                args = ["diff-tree", "-r", "--no-renames", "-z", parent, sha]
            else:
                args = ["diff-tree", "-r", "--no-renames", "-z", "--root", "--no-commit-id", sha]
            tokens = run_git(self.cwd, *args).split(b"\0")
            i = 0
            while i + 1 < len(tokens):
                meta = tokens[i].decode("ascii", "replace")
                if not meta.startswith(":"):
                    raise Fail("unexpected diff-tree output in %s" % sha[:12])
                fields = meta[1:].split(" ")
                if len(fields) < 5:
                    raise Fail("unexpected diff-tree output in %s" % sha[:12])
                _, new_mode, _, new_sha, status = fields[:5]
                path = tokens[i + 1].decode("utf-8", "surrogateescape")
                i += 2
                if status.startswith("D") or new_mode == ZERO_MODE_GITLINK or path == "LICENSE":
                    continue
                self.check_path(prefix, path)
                self.check_blob(prefix, path, new_sha)

    def close(self):
        if self._blobs:
            self._blobs.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fail when published text matches the private denylist.", add_help=True)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--tree", metavar="DIR")
    mode.add_argument("--rev", metavar="REV")
    ap.add_argument("--range", metavar="SPEC", dest="range_spec")
    ap.add_argument("--denylist", metavar="FILE")
    ap.add_argument("--show", action="store_true")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    if not (ns.tree or ns.rev or ns.range_spec):
        sys.stderr.write("check-public: nothing to scan (pass --tree, --rev or --range)\n")
        return 2
    scan = None
    try:
        deny = load_denylist(ns.denylist)
        cwd = os.getcwd()
        scan = Scan(deny, ns.tree or cwd, ns.show)
        if ns.tree:
            scan.tree(ns.tree)
        if ns.rev:
            scan.rev(ns.rev)
        if ns.range_spec:
            scan.range(ns.range_spec)
    except Fail as exc:
        sys.stderr.write("check-public: %s\n" % exc)
        return 2
    finally:
        if scan:
            scan.close()
    print("checked: %d files, %d commits, %d blobs; %d hits" % (scan.files, scan.commits, scan.blobs, scan.hits))
    return 1 if scan.hits else 0


if __name__ == "__main__":
    sys.exit(main())
