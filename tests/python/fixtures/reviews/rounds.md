# Code review: add-login

- round: 1
- scope: full diff
- verdict: findings

## Findings

### F1 — hashing is unsalted
- severity: blocker
- origin: introduced
- file: src/auth.py:12
- what: sha256 without a salt.
- why it matters: rainbow tables.
- suggested fix: scrypt.
- resolution:

### F2 — typo in a log line
- severity: should-fix
- origin: pre-existing — on base
- file: src/log.py:3
- what: spelling.
- resolution:

### F3 — naming
- severity: nit
- origin: introduced
- file: src/names.py:9
- what: a vague name.
- resolution:

## Round 2

- reviewed at: 2026-03-04T10:00Z
- F1: resolved — salted now
- F2: still open — the base has it too

### F4 — missing test
- severity: should-fix
- origin: introduced
- file: tests/test_auth.py:1
- what: no test for the salt.
- resolution:

## Check of round 2

- reviewed at: 2026-03-04T11:00Z
- F2: fixed — corrected
- F4: not fixed — still no test
