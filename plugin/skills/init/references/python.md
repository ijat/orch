# Python reference

verified: 2026-11-02
sources: <https://docs.astral.sh/uv/concepts/projects/init/>, <https://docs.astral.sh/uv/guides/integration/github/>,
<https://docs.astral.sh/uv/concepts/projects/dependencies/>, <https://docs.astral.sh/ruff/default-rules/>,
<https://docs.astral.sh/ruff/settings/>, <https://docs.astral.sh/ruff/linter/>, <https://docs.astral.sh/ruff/formatter/>,
<https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/>,
<https://packaging.python.org/en/latest/guides/writing-pyproject-toml/>, <https://docs.python.org/3/howto/logging.html>,
<https://mypy.readthedocs.io/en/stable/existing_code.html>, <https://docs.pytest.org/en/stable/how-to/usage.html>,
`gh api repos/astral-sh/setup-uv/releases/latest` (2026-11-02). Lines marked `unverified` have no primary source fetched.
Architecture rules: `references/architecture.md`.

## Init command

These are for the first plan: the `orch:init` skill writes docs and config only and does not run them. CLAUDE.md
`Commands` lists the ones that need code as `not yet runnable (no code)`.

- `uv init` makes an application by default; `uv init --lib` a library (adds `py.typed`). Both get `pyproject.toml`,
  `README.md`, `.python-version` and a `src/<name>/` package with a build system (default backend `uv_build`);
  `--no-package` gives a flat layout with no build system; `--bare` only a `pyproject.toml` (docs.astral.sh/uv/concepts/projects/init/).
- Prefer the src layout (packaging.python.org src-layout-vs-flat-layout). `pyproject.toml` always has
  `[build-system]`, `[project]` and `[tool.*]` tables (packaging.python.org writing-pyproject-toml).
- Dev dependencies: `uv add --dev ruff mypy pytest` (`--dev`/`--group` documented at docs.astral.sh/uv/concepts/projects/dependencies/).

## Layout (interfaces and adapters)

- `src/<pkg>/core/` (domain, pure logic, `ports.py` with the interfaces), `src/<pkg>/adapters/<thing>/` (implementations),
  `src/<pkg>/__main__.py` or `main.py` (composition root, the only place adapters are constructed), `tests/`.
- Interfaces: `typing.Protocol` classes (structural; adapters need not inherit) or `abc.ABC` when registration must be
  explicit. The choice is judgement; docs not fetched. Interfaces speak core types and core exceptions only.
- Contract tests: `tests/contract/test_store_contract.py` with a pytest fixture parametrised over implementations
  (`@pytest.fixture(params=[...])`, unverified) so the real adapter, the in-memory one and the fake run the same cases.
- Boundary check: a test that parses `core/**/*.py` with `ast` and fails on any import from `adapters` or a vendor
  package listed as an adapter dependency; put it in the gate. A third-party tool (import-linter) is unverified.
- Constructor injection: pass dependencies as `__init__` arguments; no module-level singletons to patch.

## Tooling

- Package manager and runner: uv (`uv sync`, `uv run`; docs.astral.sh/uv/guides/integration/github/).
- Lint and format: ruff. `ruff check`, `ruff check --fix`, `ruff format`, `ruff format --check` (ruff docs above).
- Types: mypy, `strict = true` under `[tool.mypy]` (`--strict` flag exists; mypy docs "existing code" page says to
  introduce stricter options gradually, so a new project starts strict). pyright is an alternative: unverified.
- Tests: pytest. One test: `pytest tests/test_mod.py::test_func` (docs.pytest.org usage). Run via `uv run pytest`.
- Per-file formatter for the hook (case patterns `*.py`): `uv run ruff format <file>` (ruff formatter docs above accept file paths: unverified for the exact invocation).

## Strictness from day 0

- mypy strict from the first commit; `py.typed` for libraries.
- Ruff's default rule set is now a long explicit list of rule codes (docs.astral.sh/ruff/default-rules/ on 2026-11-02),
  which differs from older guides. The ruff docs say "prefer `lint.select` over `lint.extend-select` to make your
  rule set explicit" (docs.astral.sh/ruff/linter/). So write an explicit list, e.g.
  `[tool.ruff.lint] select = ["E4", "E7", "E9", "F", "I", "B", "UP"]` (the set is judgement; check each prefix exists in the installed ruff).
- `warnings = error` in pytest config: unverified.

## Logging

Libraries: `logging.getLogger(__name__)` and a `NullHandler`; applications configure once (`dictConfig`)
(docs.python.org/3/howto/logging.html). Mapping to interfaces: the sink is the handler configuration, done only in the
composition root. Modules may use a module-level `getLogger(__name__)` (idiomatic, no state to patch); do not call
`basicConfig` in library code. Redact in a `logging.Filter` on the handler; test with a sentinel secret.

## Errors

One exception base per package (`class AppError(Exception)`) in `core/errors.py`; adapters catch vendor exceptions and
raise core ones with `raise ... from err` (chaining: unverified here, standard Python). Bare `except:` only in `main`.

## Config and secrets

One `load_config(environ)` returning a frozen dataclass (stdlib), called in `main`; fail fast on a missing key;
`.env.example` committed, `.env*` ignored and denied (`templates/snippets.md`). Secrets are a small class whose
`__repr__` and `__str__` return `***`. A settings library (pydantic-settings): unverified. Litmus: 12factor.net/config.

## Gate command

`uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`
(plus `python scripts/check_boundaries.py` and `./scripts/check-secrets.sh` when chosen).

## CI snippet notes

Write `ci.yml` only when the gate command exists after init; with no code yet, put "add CI once the gate exists" into `docs/STATE.md` next steps instead.

`astral-sh/setup-uv` latest is v10.3.3 (`gh api` 2026-11-02; the uv docs example still shows v9.0.0, so check).
Then `uv sync --locked --all-extras --dev` and the gate (both from the uv GitHub integration page). The uv docs
recommend pinning actions to a full commit SHA with a version comment; do so for `setup-uv`.

## Gotchas

- `uv init` (app) already uses src layout and a build system; `--no-package` removes both. Do not mix layouts.
- Ruff defaults changed; a pinned old rule list from a blog may name codes the installed ruff warns about. Run
  `ruff check` once on the empty project.
- `NullHandler` belongs in libraries only; adding handlers in a library steals the application's choice.
- Protocols are not checked at runtime unless `runtime_checkable` (unverified); the contract test is the real check.
- Python version: `.python-version` is generated by uv; do not hand-pick a version without checking the support window (unverified).
