# Security review: add-login

- round: 1
- verdict: findings

## Findings

### S1 — open redirect
- severity: high
- file: src/views.py:77
- fingerprint: `high | src/views.py | open-redirect`
- attack path: next= parameter.
- resolution:

### S2 — verbose error
- severity: low
- file: src/views.py:90
- resolution:

## Round 2

- S1: resolved — allow-list added
