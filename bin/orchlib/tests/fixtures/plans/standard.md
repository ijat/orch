# Plan: add-login

- tier: opus/high
- tradeoff: planner default
- repo: /home/u/Projects/myapp
- base branch: main
- planned at: 2026-03-01T10:00Z
- planned against: @@SHA@@
- status: approved

## Summary

- shape: single — one login page
- goal: users can sign in with a password.
- steps:
  1. Auth module — password check (S)
  2. Session store — cookie sessions (M)
  3. Views — login page and logout (M)
  4. Docs — README section (S)
  5. Release notes (S)
- files: 6; sensitive areas: auth

## Goal

Add a login page.

## Findings

### Verified

- `src/auth.py` hashes with sha256 and no salt (checked line 12).

- The session cookie in `src/session.py` has no expiry.

- The build in step 10 needs node 20.

- The CI config is unrelated to this task.

### Assumptions (not verified)

- Step 3 assumes the template engine escapes by default.

## Decisions

- **D1 Hashing** — use scrypt. Rejected: sha256 (no salt). Status: decided
- **D2 Cookies** — signed, 1 hour. Status: decided

## Approach

Small modules, one test file each.

## Steps

### Step 1 — Auth module

- size: S
- files: /home/u/Projects/myapp/src/auth.py, /home/u/Projects/myapp/tests/test_auth.py
- depends: none
- context:
  - /home/u/Projects/myapp/docs/design.md — the sign-in flow
  - /home/u/Projects/myapp/src/config.py
- do: write the password check.
- contract: `check(user, password) -> bool`; constant time.
- sketch: none
- done when: `pytest tests/test_auth.py` passes.

### Step 2 – Session store

- size: M
- files: /home/u/Projects/myapp/src/session.py
- depends: 1
- do: cookie sessions.
  Example plan text pasted for the implementer:

  ```
  ### Step 99 — not a step (inside a fence)
  ## Not a section either
  - size: XL
  ```
- contract: sessions expire after one hour.
- done when: `pytest tests/test_session.py` passes.

### Step 3 - Views

- size: M
- files: /home/u/Projects/myapp/src/views.py
- depends: 1, 2
- do: login and logout views.
- contract: POST /login sets the cookie.
- done when: the views test passes.

### Step 3a: Views follow-up

- size: S
- files: /home/u/Projects/myapp/src/views.py
- depends: 3
- do: redirect after login.
- contract: 302 to `next`.
- done when: the redirect test passes.

### Step 10 — Release notes

- size: S
- files: /home/u/Projects/myapp/CHANGELOG.md
- depends: 1-3
- do: write the notes.
- contract: one line per change.
- done when: the file has an entry.

## Files and areas touched

- src/auth.py, src/session.py, src/views.py (auth: sensitive)

## Parallelization

Sequential.

## Verification

### Per step (targeted)

Run from the repo root.

- Step 1: `pytest tests/test_auth.py`
- Step 2: `pytest tests/test_session.py`
- Step 3: `pytest tests/test_views.py`. Step 3a: `pytest tests/test_redirect.py`. Step 10: `grep -c entry CHANGELOG.md`.

### Final gate (once, before review)

`pytest -q` and `ruff check src`.

## Risks and unknowns

- The cookie library may change defaults (affects `src/session.py`).
- Step 10 may need a version bump.
- Unrelated: the logo is blurry.

## Revision 1 — 2026-03-02

- trigger: user feedback at the gate
- changes: step 2 is restated, step 3 gets one more case.

### Step 2 — Session store (restated; replaces the original block)

- size: M
- files: /home/u/Projects/myapp/src/session.py
- depends: 1
- do: signed cookie sessions.
- contract: sessions expire after one hour and are signed.
- done when: `pytest tests/test_session.py` passes.

### Step 3 — changes (amends)

- Add a test for a wrong password.

### Unrelated notes

- The CI badge moves to the README.
- Nothing about steps here.

## Revision 2 — 2026-03-03

- Step 3a is replaced by a redirect in step 3.
- The README is rewritten.
