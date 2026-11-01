# Swift reference

verified: 2026-11-02
sources: <https://swift.org/getting-started/cli-swiftpm/>, <https://www.swift.org/blog/announcing-swift-6/>,
<https://github.com/swiftlang/swift-format>, <https://github.com/swiftlang/swift-testing>,
<https://github.com/apple/swift-log> (README, `Sources/Logging/Docs.docc/BestPractices/003-AcceptingLoggers.md`,
`.../004-TestingLogging.md` on `main`), `gh api repos/apple/swift-log/releases/latest` (1.16.2, 2026-10-22),
`gh api repos/swift-actions/setup-swift/releases/latest` (2026-11-02).
Apple developer pages (os.Logger, strict-concurrency settings) could not be fetched as text; those lines are `unverified`.
Architecture rules: `references/architecture.md`.

## Init command

- `swift package init --name <App> --type executable` creates `Package.swift`, `Sources/<App>/`, and a test target
  (swift.org getting-started); `swift run` builds and runs it. `--type library`: unverified.
- Target layout: `Sources/<Target>/`, `Tests/<Target>Tests/`; an executable target is `.executableTarget` (research digest).
- Xcode app projects (not SwiftPM) have a different layout: put the logic in a local SwiftPM package and keep the Xcode
  target as the thin composition root. Details: unverified.

## Layout (D11 mapping)

- `<App>Core` library target: protocols, domain types, pure logic. No import of platform frameworks or adapters (UI-free,
  model: `ExternalMacAwake`). `<App>Adapters` library target (or one per boundary): implementations of the protocols
  for the system (filesystem, notifications, power, network) and for tests. `<App>` executable (or the Xcode app
  target): composition root; builds adapters and passes them in.
- Protocols belong to Core and use Core types and Core errors. Every system access goes behind a protocol with a test
  fake (`ExternalMacAwake/docs/CLAUDE.md:24-31`). Extension points are a protocol plus a catalog registered in the root
  (`TriggerRule`, `TriggerCatalog`).
- `Package.swift` target dependencies are the boundary: `Core` declares no dependency on `Adapters`. That a cyclic or
  reverse dependency fails to resolve: unverified; add a test that parses `Package.swift` for it anyway.
- Contract tests: a generic function `func runStoreContract<S: Store>(_ make: () -> S)` in a `<App>TestSupport` target,
  called from each adapter's tests (including the fake). Judgement; the pattern needs no framework feature.
- Constructor injection of protocol values (`any Store` or generics); no singletons, no static mutable state.

## Tooling

- Format and lint: `swift-format`, included in the Swift 6 toolchain (Xcode 16+) and run as `swift format`
  (github.com/swiftlang/swift-format). Format in place: `swift format -i -r Sources Tests`; check:
  `swift format lint --strict --recursive Sources Tests` (`-s/--strict` makes warnings fail; `-r/--recursive`; `-i/--in-place`).
  Config file name `.swift-format`: unverified.
- Tests: **Swift Testing** (`import Testing`, `@Test`, `#expect`; swiftlang/swift-testing); run with `swift test`
  (command: unverified on the pages read). XCTest coexistence: unverified.
- Build and run: `swift build`, `swift run`.

## Strictness from day 0

Swift 6 language mode turns data-race-safety checks into compile errors; in 5.10 they were warnings behind
`-strict-concurrency=complete` (swift.org/blog/announcing-swift-6). Start new packages in Swift 6 mode
(`swift-tools-version: 6.0` and `swiftLanguageModes: [.v6]` in `Package.swift`: key names unverified). Avoid `try!`, `!`
force-unwraps and `@unchecked Sendable` outside the adapters, each with a comment. Lint-rule names for these: unverified.

## Logging

Use `swift-log` (API package; you also choose a backend; README: "SwiftLog is an API package"). Library rule from the
package's best-practices docs: accept a `Logger` parameter or read the task-local `Logger.current`; never construct your
own logger inside a library; the application chooses backend, level and metadata. Applications bootstrap the backend once
in the composition root (`LoggingSystem.bootstrap`). In tests, capture with `InMemoryLogHandler` from the
`InMemoryLogging` product and never call `LoggingSystem.bootstrap` (it is process-wide and traps if called twice).
Those docs are on `main`; confirm `Logger.current` and `InMemoryLogging` exist in the release you pin (1.16.2 on
2026-11-02). Apple's `os.Logger`: unverified. Redact in the handler; test with a sentinel secret.

## Errors

Swift error convention for new code: unverified (gap in the survey). Working rule: `enum <App>Error: Error` in Core,
adapters convert system errors (`NSError`, `POSIXError`) into it, `throws` through the protocol; typed throws only if the
chosen language mode supports it (unverified).

## Config and secrets

Config as one struct built in the composition root from the environment or a plist (`ProcessInfo` use: unverified).
Secrets: on Apple platforms use the Keychain behind a `SecretStore` protocol (Keychain API: unverified); never in the
repo, `Info.plist` or logs. `.env*` ignored and denied (`templates/snippets.md`).

## Gate command

`swift format lint --strict --recursive Sources Tests && swift build && swift test`
(plus `./scripts/check-boundaries.sh` and `check-secrets.sh` when chosen).

## CI snippet notes

macOS runners carry Xcode and so `swift`; Linux needs a toolchain step. `swift-actions/setup-swift` latest release v2.4.0
(published 2025-11-19, `gh api`); whether it supports the current Swift is unverified, so check its README before use.
Runner image names and installed Xcode versions: unverified.

## Gotchas

- `swift format` exists only in Swift 6 / Xcode 16 and later; older toolchains need `brew install swift-format`.
- `LoggingSystem.bootstrap` may be called once per process; a second call traps.
- Apple-only APIs (AppKit, IOKit, UserNotifications) in Core break Linux CI; keep them in adapters.
- Sendable and actor isolation errors surface at the protocol boundary; mark protocols `Sendable` deliberately, not by habit.
- GUI/Xcode projects: no CLI scaffold is documented in the pages read; write the SwiftPM package first.
