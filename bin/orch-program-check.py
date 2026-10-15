#!/usr/bin/env python3
"""Check a program roadmap's milestone table against the program-mode floors.

Usage: orch-program-check.py <roadmap.md>
Prints one line per finding (FAIL / WARN) and a final verdict line. Exit 0 = no FAIL, 1 = FAIL,
2 = unreadable or no milestone table. Rules and their reasons: ~/.claude/skills/orchestrate/PROGRAM.md.
"""
import re, sys

COLS = ["m", "depends", "mode", "tiers", "sensitive", "review", "merge", "you", "size", "confidence"]
HEADER = ["M", "goal", "depends on", "mode", "tiers", "sensitive", "review", "merge", "you", "size", "confidence"]
WEIGHT = {"S": 1, "M": 2, "L": 4}
SPAN_WEIGHT_WARN = 8      # heuristic only: no size effect on review rounds was found (n=14, 2026-10-15)
SPAN_LEN_WARN = 3         # more milestones than this in one review span → WARN


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*?)\s*$", line)
        if m:
            cur = m.group(1).strip().lower(); out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return out


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_table(lines, fails):
    rows, header_seen = [], False
    for ln in lines:
        if not ln.strip().startswith("|"):
            continue
        c = cells(ln)
        if not header_seen:
            if [x.lower() for x in c] != [h.lower() for h in HEADER]:
                fails.append(f"milestone table header must be: | {' | '.join(HEADER)} |")
                return None
            header_seen = True; continue
        if all(re.fullmatch(r":?-{3,}:?", x) for x in c):
            continue
        if len(c) != len(HEADER):
            fails.append(f"row has {len(c)} cells, expected {len(HEADER)}: {ln.strip()}"); continue
        r = dict(zip(["m", "goal"] + COLS[1:], c))
        r["raw"] = ln.strip()
        rows.append(r)
    return rows if header_seen else None


def main():
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[2]); return 2
    try:
        text = open(sys.argv[1], encoding="utf-8").read()
    except OSError as e:
        print(f"ERROR cannot read {sys.argv[1]}: {e}"); return 2
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)   # guidance comments are not content
    sec = sections(text)
    fails, warns = [], []
    if "milestones" not in sec:
        print("ERROR no '## Milestones' section"); return 2
    rows = parse_table(sec["milestones"], fails)
    if rows is None or (not rows and not fails):
        for f in fails: print("FAIL", f)
        print("ERROR no milestone table rows"); return 2

    # --- value validation ---
    ms = []
    for r in rows:
        bad = []
        if not re.fullmatch(r"\d+", r["m"]): bad.append("M")
        if r["mode"] not in ("standard", "lite"): bad.append("mode")
        if r["review"] not in ("-", "code", "code+sec"): bad.append("review")
        if r["merge"] not in ("-", "yes"): bad.append("merge")
        if r["size"] not in WEIGHT: bad.append("size")
        if r["confidence"] not in ("high", "med", "low"): bad.append("confidence")
        if r["depends"] != "-" and not re.fullmatch(r"\d+(\s*,\s*\d+)*", r["depends"]): bad.append("depends on")
        if bad:
            fails.append(f"M{r['m']}: invalid {', '.join(bad)} — {r['raw']}"); continue
        r["n"] = int(r["m"])
        r["deps"] = [] if r["depends"] == "-" else [int(x) for x in r["depends"].split(",")]
        ms.append(r)
    nums = [r["n"] for r in ms]
    if ms and nums != list(range(1, len(ms) + 1)):
        fails.append(f"milestones must be numbered 1..N in run order, got {nums}")
    known = set(nums)
    by_n = {r["n"]: r for r in ms}
    reviewed = [r["n"] for r in ms if r["review"] != "-"]

    for r in ms:
        n = r["n"]
        # F: never merge unreviewed code
        if r["merge"] == "yes" and r["review"] == "-":
            fails.append(f"M{n}: merge point without a review point — unreviewed code would reach the base branch")
        # F: sensitive areas reviewed in place
        if r["sensitive"] != "-":
            if r["review"] == "-":
                fails.append(f"M{n}: touches {r['sensitive']} but is not reviewed in place")
            elif r["review"] == "code":
                warns.append(f"M{n}: sensitive ({r['sensitive']}) with code review only — fine only if the user declined the security reviewer")
            if r["mode"] == "lite":
                warns.append(f"M{n}: lite mode on a sensitive milestone (lite rules exclude sensitive areas)")
            if "haiku" in r["tiers"]:
                warns.append(f"M{n}: haiku tier on a sensitive milestone")
        # F: dependencies point backwards at existing milestones
        for d in r["deps"]:
            if d not in known:
                fails.append(f"M{n}: depends on M{d}, which does not exist")
            elif d >= n:
                fails.append(f"M{n}: depends on M{d}, which runs at or after it")
        # W: tier/size mismatches
        if r["size"] == "S" and re.search(r"opus/max|fable", r["tiers"]):
            warns.append(f"M{n}: {r['tiers']} on an S milestone")
        if r["mode"] == "lite" and r["size"] != "S":
            warns.append(f"M{n}: lite on a size-{r['size']} milestone (lite rules: ≤ ~5 files, ≤ 3 steps)")

    if ms:
        last = ms[-1]
        if last["review"] == "-" or last["merge"] != "yes":
            fails.append(f"M{last['n']} (last): must be a review point and a merge point")

    # F: a contract's producer is reviewed before its first consumer starts
    for ln in sec.get("contracts between milestones", []):
        m = re.match(r"^\s*-\s*(C\d+)\s*\(\s*M(\d+)\s*(?:→|->)\s*(M\d+(?:\s*,\s*M\d+)*)\s*\)", ln)
        if not m:
            continue
        cid, p = m.group(1), int(m.group(2))
        cons = [int(x) for x in re.findall(r"M(\d+)", m.group(3))]
        missing = [x for x in [p] + cons if x not in known]
        if missing:
            fails.append(f"{cid}: names missing milestone(s) {', '.join('M%d' % x for x in missing)}"); continue
        first = min(cons)
        if first <= p:
            fails.append(f"{cid}: consumer M{first} runs at or before producer M{p}"); continue
        if not any(p <= k < first for k in reviewed):
            fails.append(f"{cid}: M{p}'s contract is not reviewed before M{first} builds on it (need a review point in M{p}..M{first - 1})")

    # W: review spans — too long, too heavy, or every milestone reviewed
    start = 1
    for k in reviewed:
        span = [by_n[i] for i in range(start, k + 1) if i in by_n]
        if len(span) > SPAN_LEN_WARN:
            warns.append(f"review at M{k} covers {len(span)} milestones (M{start}–M{k})")
        w = sum(WEIGHT[r["size"]] for r in span)
        if w > SPAN_WEIGHT_WARN:
            warns.append(f"review at M{k} covers size weight {w} (S=1 M=2 L=4; heuristic limit {SPAN_WEIGHT_WARN})")
        start = k + 1
    if len(ms) >= 4 and len(reviewed) == len(ms):
        warns.append("every milestone is a review point — over-review; batch the low-risk ones")

    # W: shape sanity — a roadmap that is really a single plan
    if 0 < len(ms) <= 2:
        warns.append(f"only {len(ms)} milestone(s) — consider a single plan")
    elif ms and not any(r["review"] != "-" or r["merge"] == "yes" or r["you"] != "-" for r in ms[:-1]):
        warns.append("no review, merge or user stop before the last milestone — this is a single plan with headings")

    # W: low confidence needs a probe
    probes = "\n".join(sec.get("premises and probes", []))
    for r in ms:
        if r["confidence"] == "low" and not re.search(rf"\bM{r['n']}\b", probes):
            warns.append(f"M{r['n']}: confidence low but no M{r['n']} entry in 'Premises and probes'")

    for f in fails: print("FAIL", f)
    for w in warns: print("WARN", w)
    print(f"{'FAIL' if fails else 'OK'}: {len(ms)} milestones, review points {['M%d' % k for k in reviewed]}, "
          f"{len(fails)} fail, {len(warns)} warn")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
