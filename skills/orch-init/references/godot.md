# Godot (GDScript) reference

verified: 2026-11-02
sources: <https://docs.godotengine.org/en/stable/tutorials/best_practices/project_organization.html>,
<https://docs.godotengine.org/en/stable/tutorials/best_practices/scene_organization.html>,
<https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/static_typing.html>,
<https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/gdscript_styleguide.html>,
<https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/gdscript_basics.html>,
<https://docs.godotengine.org/en/stable/tutorials/editor/command_line_tutorial.html>,
<https://github.com/bitwes/Gut> README, <https://gut.readthedocs.io/en/latest/Command-Line.html>,
<https://github.com/Scony/godot-gdscript-toolkit> README, `gh api` releases for godotengine/godot (4.7.2-stable),
bitwes/Gut (v9.6.1), Scony/godot-gdscript-toolkit (4.5.0), chickensoft-games/setup-godot (v2.4.3), all 2026-11-02.
Lines marked `unverified` have no primary source fetched. Architecture rules: `references/architecture.md`.

## Init command

These are for the first plan: `orch-init` writes docs and config only and does not run them. CLAUDE.md
`Commands` lists the ones that need code as `not yet runnable (no code)`.

- No CLI scaffold is documented in the pages read. A project is a folder with `project.godot`; create it from the Project
  Manager, or the agent writes a minimal `project.godot` and opens it in the editor once (`config_version` and the
  minimal keys: unverified). Run the project with `godot --path <dir>`; use `--headless` where there is no GPU
  (CI); `--headless` is `--display-driver headless --audio-driver Dummy` (command-line tutorial).
- Install third-party code only under `addons/` (project organization page): GUT in `addons/gut/`.

## Layout (interfaces and adapters)

- `core/`: pure GDScript, no `Node` subclasses, no scene tree; logic plus the **interfaces**. GDScript has no interface
  keyword. Use a base class with `class_name` and abstract methods via the `@abstract` annotation (since Godot 4.5,
  gdscript_basics page), or a plain base `RefCounted` class on older versions. Test `core/` before any scene is wired.
- `adapters/<thing>/`: implementations (save-file storage, network, platform/OS, input, audio, clock, log sink).
- `scenes/`: thin; scripts only receive what they need. The scene docs recommend dependency injection: the parent or a
  higher-level API supplies dependencies by signal connection, a `Callable`, a `NodePath` or a node reference; signals
  "respond" to behaviour, they do not start it; keep data internal to a scene where possible (scene organization page).
- `autoloads/`: globals. Treat them as the **composition root only**: one `App` autoload builds the adapters and hands
  them to `core/` objects (judgement). Other autoloads (a `Log` with category tags) are
  sinks behind an interface. Do not call autoloads from `core/`.
- Assets next to the scenes that use them; `snake_case` for files and folders, `PascalCase` for node names and
  `class_name`s; third-party in `addons/`; add `.gdignore` to folders Godot must skip (project organization and
  style guide pages).
- Contract tests: a GUT base script `test/contract/contract_store.gd` holds the cases; one script per implementation
  extends it and overrides `create_store()` (GUT script inheritance: unverified). GUT also doubles and mocks for fakes
  (GUT docs navigation lists Doubling and Mocking; usage: unverified).
- One code path for single and multiplayer: the transport is an adapter.

## Tooling

- Language server and editor: Godot's own. Format and lint: `gdtoolkit` (`pip install "gdtoolkit==4.*"` for Godot 4, or
  `pipx`); `gdformat --check <dir>` and `gdlint <dir>` (README; the GitHub action in the README runs both). Latest release
  4.5.0 (2025-10-09), older than Godot 4.7.2; whether it parses every 4.7 syntax: unverified, so run it on the empty project.
- Tests: **GUT**. Versions must match Godot: GUT 9.x is for Godot 4.x; README table: 9.7.1 (branch `godot_4_7`) for 4.7.x,
  9.6.1 for 4.6.x. Install into `addons/gut/` (Asset Library or release zip), enable the plugin in Project Settings.
- Package manager: none for GDScript. Python `pip`/`pipx` only for gdtoolkit; use `uv tool install gdtoolkit` if the user uses
  uv (command: unverified).
- Per-file formatter for the hook (case patterns `*.gd`): `gdformat <file>` (gdtoolkit README, `gdformat` formats in place; per-file use: unverified).

## Strictness from day 0

Typed GDScript only. Godot's typed-GDScript warnings are off by default; enable them in Project Settings under
Debug > GDScript (turn on Advanced Settings): `UNTYPED_DECLARATION` to require types everywhere, optionally
`INFERRED_DECLARATION`; the `UNSAFE_*` warnings flag unsafe operations (static typing page). The page says to stick to
one style, typed or dynamic. Treat warnings as errors: a project setting for that exists but its name is unverified.

## Logging

A `Log` interface in `core/` (`debug|info|warn|error(category, msg, fields)`), an autoload sink behind it, category tags. Engine print helpers (`print`, `push_warning`, `push_error`): unverified. No secrets or player
personal data in logs; redact at the sink; test with a sentinel. "Logs a lot" means more debug events behind a level switch.

## Errors

GDScript has no exceptions: unverified (standard language fact, not read from the pages fetched). Working rule: functions that
can fail return a result object (`ok`, `value`, `error`) or Godot's `Error` enum; adapters convert engine errors at the
boundary; `assert` only for programmer errors.

## Config and secrets

Settings in a `ConfigFile` or `ProjectSettings` (API: unverified), read once in the composition root; secrets never in
`project.godot`, scenes, resources or export presets (export preset contents: unverified, so check before commit). Reading
the environment from an exported build: unverified; a gitignored config resource is the working alternative. `.env*` ignored and denied
(`templates/snippets.md`).

## Gate command

`gdformat --check core adapters scenes autoloads test && gdlint core adapters scenes autoloads test && godot --headless -d -s --path "$PWD" addons/gut/gut_cmdln.gd -gdir=res://test -ginclude_subdirs -gexit`
(`-d`, `-s`, `--path`, `-gdir`, `-ginclude_subdirs`, `-gexit` are GUT command-line options; exit code 0 if all pass,
1 if any fail; `--headless` on CI. `res://test` is the folder convention, unverified).

## CI snippet notes

Write `ci.yml` only when the gate command exists after init; with no code yet, put "add CI once the gate exists" into `docs/STATE.md` next steps instead.

Options: `Scony/godot-gdscript-toolkit@master` action for static checks (in its README); `chickensoft-games/setup-godot`
latest v2.4.3 to install the engine (`gh api`; its inputs: unverified); or download the Linux headless binary. Pin the Godot
version in CI to the one in `project.godot` features. Import the project once before tests (`godot --headless --import`,
unverified) because `.godot/` is not committed.

## Gotchas

- GUT, gdtoolkit and Godot minor versions must line up; a mismatch can fail in confusing ways (GUT's README ties each GUT version to a Godot version).
- Autoloads are globals: they are the right home for the composition root and the wrong home for logic.
- `.gdignore` hides a folder from import; use it for docs and tooling folders inside the project.
- `.godot/` (import cache) is generated: gitignore it (generated by the engine; confirm for the version used).
- `@abstract` needs Godot 4.5+; check the engine version before using it.
- Typed and untyped code mixed in one project defeats the warnings; decide on day 0.
