# Architecture reference: swappable interfaces at every boundary

Language-neutral. Used by the `orch-init` agent to name boundaries for a project and to write the
`Architecture rules` section of its CLAUDE.md (<= 10 lines). Ecosystem commands, layouts and idioms live
in `references/<ecosystem>.md`, not here. Examples cite the user's own projects (under `~/Projects`) as
illustrations of the pattern, not as requirements; line numbers are from a grep-based survey on
2026-11-02 and may have moved. Claims marked `[unsourced]` are working judgement with no primary source.

## 1. Primary rule: a swappable interface at every boundary (D11)

Anything **external or replaceable** sits behind an interface (trait / protocol / interface / abstract
type) **owned by the core**. Implementations live in an adapter layer outside the core. One
**composition root** wires them.

What counts as a boundary (each is a swap point from day 0):

- storage (database, files, cache, queue)
- network and API clients (HTTP, RPC, a vendor SDK)
- third-party libraries whose replacement is conceivable (parser, hasher, codec, renderer)
- platform and OS access (filesystem, process, windowing, notifications, permissions)
- UI toolkit and I/O surfaces (CLI, GUI, web handler, bot transport)
- clock, randomness, identifiers (so tests and replays are deterministic)
- logging sink and metrics sink

The mechanics, in order:

1. **Interface owned by the core.** It speaks the core's types and the core's errors, never a vendor's
   types. If a vendor type leaks through the interface, the boundary is not real.
2. **Adapters outside.** `adapters/<thing>/` (or the ecosystem's equivalent crate / package / project).
   An adapter depends on the core; the core never depends on an adapter.
3. **One composition root.** The only place that constructs adapters and passes them in
   (`main`, an `AddInfrastructure()` extension, an `AppContext`). Nothing else calls an adapter constructor.
4. **Constructor injection.** Dependencies arrive as constructor/function parameters, not globals,
   singletons or `init()` side effects. Nothing global to patch in tests.
5. **A contract (conformance) test per interface.** One shared suite every implementation must pass
   (the real adapter, the in-memory one, a fake). A new implementation is "done" when it passes.
6. **A fake for tests.** Each interface ships an in-memory/fake implementation next to the contract test;
   core tests use it. The fake is itself checked by the contract suite.
7. **Extension means addition.** Adding a driver, provider, codec, connector, trigger or content pack
   is a new adapter plus a registration line in the composition root; the core does not change.
   If it would, the interface is wrong.

Rejected: an interface for every internal class. That is ceremony without a swap point; keep concrete
types inside the core.

Examples (illustrations, paths from the survey):
- Rust, interfaces as traits plus registries by id: `chonkfs/CLAUDE.md:55-60` (codecs), conformance in
  separate crates outside the product build.
- .NET, ports and adapters with one DI extension per layer: `nefpgp/CLAUDE.md:48`; provider contract
  `IAiSessionProvider` in Lunegit; clock as `IUtcClock` in DoomLearner.
- Go, small consumer-side interfaces: `Debugger/internal/session/driver.go:17`; frozen store contract
  `shutter/internal/store/contract.go`.
- TypeScript, plugin SDK with a conformance helper: `Halo/packages/connector-sdk/src/conformance.ts`;
  context-object DI `lunieum-browser/.../AppContext.ts:9`.
- Swift, every system access behind a protocol with test fakes: `ExternalMacAwake/docs/CLAUDE.md:24-31`.

## 2. Compatible patterns (keep these alongside the primary rule)

1. **Pure core / thin shell.** Logic that decides lives in code with no I/O; the shell reads input, calls
   the core, writes output. Test the core before wiring anything. Examples: `MartGame` `core/` has no
   nodes and is tested before scenes (`CLAUDE.md:52-67`); `ExternalMacAwake` UI-free library target.
2. **Dependency injection by constructor.** See 1.4. No DI container required: Go and small Rust/Swift
   projects pass arguments by hand (`mustang-webui/CLAUDE.md:54`: stdlib first, no framework).
3. **Inward-only dependencies, enforced.** `adapter -> core`, never the reverse; checked by a test or a
   script in the gate (section 4). Examples: `chonkfs/CLAUDE.md:55`; `Luneterm/scripts/check-lib-boundaries.sh`;
   `Halo/apps/server/test/architecture.test.ts`.
4. **Typed config from the environment.** One loader reads the environment once into a typed struct;
   no other code reads env vars; secrets are a distinct type that cannot print itself; `.env.example`
   lists every key with no real values. Litmus: the repo could be open-sourced at any moment without
   exposing a credential (<https://12factor.net/config>). Example: `sangbots-v3/.../sm3-config/src/lib.rs:13`
   (typed loader, `Secret` newtype).
5. **Injected logger with redaction.** See section 5.
6. **ADRs and numbered invariants.** Decisions in an append-only log with what was rejected; invariants
   numbered in CLAUDE.md so they can be cited. Examples: `Luneterm`/`chonkfs`/`lunieum` `DECISIONS.md`;
   MADR files under `docs/adr` in the Go projects.
7. **Determinism and golden tests where output must be stable.** Identical input gives identical output;
   goldens for CLI/terminal output; replay/verify mode for simulations. Examples: `RezekiApp`
   `purity.test.ts` and `sim --verify`; `Luneterm` xterm goldens; `PunchHole` cross-language vectors.
   Use the clock/randomness interfaces from section 1 to make this possible.
8. **Loopback by default for anything that listens.** A server binds `127.0.0.1` unless the user asks
   otherwise. [unsourced; recurring practice in the surveyed projects]

## 3. Reconciling YAGNI (D11)

"Add the seam when the second implementation appears" (rule of three) is sound **inside the core**:
internal classes stay concrete until a second variant exists. [unsourced; no primary source found]

At **boundaries the seam exists from day 0**, even with one implementation, because the user requires
every external or replaceable thing to be swappable. The contract test and the fake are the second
implementation that exists on day 0, so the seam is exercised immediately.

Cost control: a boundary interface is small and shaped by what the core needs, not by what the vendor
offers. Both Go and Rust treat exposed seams as API commitments: Go's `%w` makes the wrapped error part
of the API (<https://go.dev/blog/go1.13-errors>), and Cargo features must be additive
(<https://doc.rust-lang.org/cargo/reference/features.html>). So the interface stays minimal and is not
widened speculatively.

## 4. Rules in CLAUDE.md versus enforced checks

CLAUDE.md is advice that costs context on every turn; anything that must always hold belongs in a check.
Context files should name only what an agent cannot infer (<https://code.claude.com/docs/en/best-practices>),
and the file stays under ~200 lines (<https://code.claude.com/docs/en/memory>). So:

| Concern | In CLAUDE.md (one line) | Enforced by (prefer) |
|---|---|---|
| Dependency direction | "Dependencies point inward only" | architecture test or boundary script in the gate |
| No vendor types in the core | the rule + the check's name | the same import-boundary check |
| Adapters only built in the composition root | the rule | the same check (grep for adapter constructors outside the root) |
| Every interface has a contract test | the rule | a test that lists interfaces and fails if one has no contract suite |
| Never log secrets | the rule | redacting logger + a test that logs a sentinel and asserts it is absent |
| Never read `.env` / secrets | the rule | `permissions.deny` in `.claude/settings.json` (templates/snippets.md) |
| Formatting / lint | the command | formatter hook + CI gate |
| Unsafe/escape hatches | the rule | the ecosystem's lint-level (see `references/<eco>.md`) |

When the user did not pick settings and hooks, the checks still exist as scripts or tests in the gate.
Emphasis ("IMPORTANT", "NEVER") is not enforcement.

## 5. Logging, errors, config

**Logging.** Injected, never global (Go `Debugger/AGENTS.md:66-80`: no globals, no `init`). Libraries
emit through the logging facade and never configure it; the binary configures it once. This is the
convention in the surveyed ecosystems: Python libraries use `getLogger(__name__)` plus a `NullHandler`
and applications configure once (<https://docs.python.org/3/howto/logging.html>); Rust libraries never
call `set_global_default`, the binary does (<https://docs.rs/tracing/latest/tracing/>); .NET uses
`ILogger<T>` through DI with message templates, not string interpolation
(<https://learn.microsoft.com/en-us/dotnet/core/extensions/logging>); Go uses `log/slog`
(<https://go.dev/blog/slog>). Rules for every project:
- levels with meaning: error = needs a human, warn = degraded but handled, info = lifecycle, debug/trace = diagnosis.
- structured fields, a category per component (`MartGame` `Log` autoload tags by category, `log.gd:45-53`).
- redaction by field name at the sink: never log secrets, passphrases, tokens, or user/debuggee data
  (a redaction rule recurs across the surveyed projects). The logging sink is itself a boundary interface (section 1).
- when the user asked for "logs a lot", that means more diagnosis-level events behind a level switch,
  not more info-level noise.

**Errors.** Follow the ecosystem convention, linked from `references/<eco>.md`; the cross-language rules
are: errors at a boundary are the core's own error type, wrapped with the cause kept; one error type per
library, a catch-all only in the binary (Rust: `thiserror` in libs, `anyhow` in bins, `chonkfs/CLAUDE.md:62`;
Rust errors implement `std::error::Error + Send + Sync`, <https://rust-lang.github.io/api-guidelines/interoperability.html#c-good-err>;
Go wraps with `%w` and wraps only when callers should inspect it, <https://go.dev/blog/go1.13-errors>).
Do not swallow; do not use errors for normal control flow.

**Config.** Environment variables, one typed loader at the composition root, fail fast at startup on a
missing or malformed required key, a documented default for every optional key, `.env.example` committed,
real `.env*` gitignored and denied to the agent (<https://12factor.net/config>; `templates/snippets.md`).
Secrets arrive only through config and are a non-printing type. Budget or scope limits that matter
(a free-tier cap, offline-only) go in CLAUDE.md as hard rules and in a check where one can exist
(`infra-monitor/CLAUDE.md:11`).

## 6. What the agent writes from this file

Into CLAUDE.md `Architecture rules` (<= 10 lines): the project's boundaries named concretely (interface ->
adapter dir), the composition-root file, the inward-only rule with its check, the contract-test location,
and how to add a new implementation. Into `docs/ARCHITECTURE.md` (only when the project has more than
three boundaries or more than one deployable): the interface list, the dependency diagram and the
extension recipes. Never a restatement of the file layout the code already shows.
