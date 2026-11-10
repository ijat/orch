# Rust reference

verified: 2026-11-02
sources: <https://doc.rust-lang.org/cargo/commands/cargo-new.html>, <https://doc.rust-lang.org/cargo/reference/workspaces.html>,
<https://doc.rust-lang.org/cargo/reference/resolver.html>, <https://doc.rust-lang.org/cargo/reference/features.html>,
<https://doc.rust-lang.org/cargo/reference/manifest.html#the-lints-section>, <https://doc.rust-lang.org/cargo/commands/cargo-fmt.html>,
<https://doc.rust-lang.org/cargo/commands/cargo-test.html>, <https://doc.rust-lang.org/clippy/usage.html>,
<https://github.com/rust-lang/rustfmt>, <https://github.com/dtolnay/thiserror>, <https://rust-lang.github.io/api-guidelines/interoperability.html#c-good-err>,
<https://docs.rs/tracing/latest/tracing/>, `gh api repos/Swatinem/rust-cache/releases/latest` (2026-11-02).
Lines marked `unverified` have no primary source fetched. Architecture rules: `references/architecture.md`.

## Init command

These are for the first plan: the `orch:init` skill writes docs and config only and does not run them. CLAUDE.md
`Commands` lists the ones that need code as `not yet runnable (no code)`.

- `cargo new <name>` (binary, the default) or `cargo new --lib <name>`; the default edition is 2024
  (cargo-new page). For a workspace: root `Cargo.toml` with `[workspace]`, `members = ["crates/*"]`, then
  `cargo new --lib crates/<app>-core` etc.
- A virtual workspace needs an explicit `resolver` (cargo workspaces page); `"3"` is the edition-2024 default and
  needs Rust 1.84+ (cargo resolver page); `"2"` for edition 2021.
- `[workspace.dependencies]` for shared versions; members use `dep = { workspace = true }` (cargo workspaces page).

## Layout (interfaces and adapters)

- `crates/<app>-core`: traits and pure logic; depends on no adapter and on no I/O crate. `crates/<app>-<thing>`: one adapter
  crate per boundary (fs, http, db). `crates/<app>-cli` (or `-server`): the binary, the composition root, the only crate
  that depends on adapters. Optional `crates/<app>-testkit`: contract suites and fakes (a testkit crate shared by all adapters).
- Interfaces are traits owned by `core`, using core types and the core error enum. Extension points are traits plus a
  registry by id (a codec or driver registry); adding an implementation is a new crate and one registration line.
- The crate graph is the boundary: `core/Cargo.toml` listing no adapter makes the reverse dependency a compile error.
  Check what the graph cannot: `scripts/check-boundaries.sh` greps `core/Cargo.toml` and `core/src` for banned crates
  (a small script in the gate).
- Contract tests: `pub fn run_store_contract<S: Store>(make: impl Fn() -> S)` in `testkit`; each adapter's `tests/`
  calls it. Keep the testkit and fuzz/conformance crates out of the product build.
- Features must be additive (cargo features page); never use a feature to switch an adapter on or off in a way that
  changes core behaviour. Targets: `lib.rs`, `main.rs`, `src/bin/`, `tests/`, `examples/`, `benches/` (cargo-targets page).

## Tooling

- Format: `cargo fmt --all` / `cargo fmt --all -- --check` (rustfmt README); `rustfmt` is an optional toolchain component
  (`rustup component add rustfmt`). Config `rustfmt.toml` or `.rustfmt.toml`.
- Lint: `cargo clippy` (clippy usage page). Test: `cargo test --workspace`; one test `cargo test -p <crate> <name>`
  (`cargo test [options] [testname]`, cargo-test page). A faster runner (cargo-nextest): unverified.
- Pin the toolchain with `rust-toolchain.toml` (file name: unverified here).
- Per-file formatter for the hook (case patterns `*.rs`): `rustfmt <file>` (rustfmt README; per-file invocation: unverified, confirm with `rustfmt --help`).

## Strictness from day 0

- Workspace lints, inherited by every member (cargo manifest page): in the root
  `[workspace.lints.rust] unsafe_code = "forbid"` and `[workspace.lints.clippy]` with the groups the user wants; in each member
  `[lints] workspace = true`. Do not add `#![deny(warnings)]` in source; deny warnings in the gate instead.
- Warnings denied in CI: `cargo clippy --workspace --all-targets -- -D warnings`. The clippy page also documents
  `CARGO_BUILD_WARNINGS=deny` (Cargo 1.97 and later) and warns that `-- -Dwarnings` invalidates build caches.
  `--all-targets` is judgement (the page mentions it only for `--fix`).
- Unsafe only in an FFI crate with `// SAFETY:` comments.

## Logging

`tracing` facade. Libraries emit events and never call `set_global_default`; the binary installs one subscriber once
(docs.rs/tracing). The subscriber is the logging sink boundary. Redaction: log fields by name; secrets are a
`Secret<T>` newtype whose `Debug`/`Display` print `[redacted]` (a `Secret` type in a config crate). Test with a
sentinel. `tracing-subscriber` API: unverified.

## Errors

Error types implement `std::error::Error + Send + Sync` (api-guidelines C-GOOD-ERR). `thiserror` derives the trait in
libraries (thiserror README); `anyhow` only in the binary (`anyhow` docs: unverified). One error
enum per crate; adapters convert vendor errors into the core enum with `#[from]` or `map_err`, keeping the source.

## Config and secrets

One typed loader in a `<app>-config` module or crate, called in `main`, fail fast, `Secret` newtype,
`.env.example` committed, `.env*` ignored and denied (`templates/snippets.md`). Litmus: 12factor.net/config.

## Gate command

`cargo fmt --all -- --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace && ./scripts/check-boundaries.sh`
(add `./scripts/check-secrets.sh` when chosen). Wrap it in `scripts/gate.sh`.

## CI snippet notes

Write `ci.yml` only when the gate command exists after init; with no code yet, put "add CI once the gate exists" into `docs/STATE.md` next steps instead.

Toolchain step: `dtolnay/rust-toolchain` (latest release tag is `v1`, published 2022; branch names such as `@stable`
and `components:` input: unverified) or the runner's preinstalled rustup (unverified). Cache: `Swatinem/rust-cache`
latest v2.9.3 (`gh api` 2026-11-02). Then the gate command. Installing `rustfmt` and `clippy` components must be explicit.

## Gotchas

- A virtual workspace needs an explicit `resolver` (cargo workspaces page; what happens without it: unverified); set it (3 for edition 2024).
- `-D warnings` on the command line rebuilds dependencies; keep one lint invocation shape in local and CI.
- `cargo fmt` is not built in; a fresh toolchain may lack it.
- Workspace lint tables apply only to members that opt in with `[lints] workspace = true`.
- Features are additive across the whole dependency graph; a "mutually exclusive" feature pair breaks builds.
- Do not publish `testkit`/fuzz crates (`publish = false`, key name: unverified).
