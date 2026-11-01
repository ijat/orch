# Go reference

verified: 2026-11-02
sources: <https://go.dev/doc/modules/layout>, <https://pkg.go.dev/cmd/go>, <https://pkg.go.dev/cmd/gofmt>,
<https://go.dev/blog/go1.13-errors>, <https://go.dev/blog/slog>, <https://go.dev/doc/articles/race_detector>,
<https://staticcheck.dev/docs/getting-started/>, <https://staticcheck.dev/docs/configuration/>,
`gh api repos/actions/setup-go/releases/latest` (2026-11-02). Lines marked `unverified` have no primary source fetched.
Architecture rules: `references/architecture.md`.

## Init command

These are for the first plan: `orch-init` writes docs and config only and does not run them. CLAUDE.md
`Commands` lists the ones that need code as `not yet runnable (no code)`.

- `go mod init <module-path>` in the project directory (pkg.go.dev/cmd/go: writes `go.mod`, must not exist, one optional argument).
- Layout (go.dev/doc/modules/layout): supporting packages go in `internal/` ("prevents other modules from depending
  on packages we don't want to expose"); commands in `cmd/<name>/main.go`; `_test.go` files beside the code. For a
  server the page recommends keeping the Go logic in `internal/` and all commands in `cmd/`. The page does not
  mention a `pkg/` directory; do not create one.

## Layout (interfaces and adapters)

- `internal/<capability>/` is the core: types, logic and the interfaces it needs, declared in the package that uses
  them (`ports.go`). Interfaces are small and consumer-side (a small driver interface in the session package; a frozen
  contract file per store is a good pattern).
- `internal/adapters/<thing>/` (or `internal/<thing>store/`): implementations. They import the core, never the reverse.
- `cmd/<name>/main.go` is the composition root and contains wiring only: it builds adapters, passes them to
  constructors, starts the program. No DI framework: constructors take interfaces.
- Contract tests: `internal/<capability>/contracttest` package exporting `func Run(t *testing.T, make func(t *testing.T) Store)`;
  each adapter's `_test.go` calls it with its constructor, and so does the in-memory fake in `internal/<capability>/fake`.
- Boundary check: `internal/` blocks other modules (layout page) but not other packages in the same module. Enforce
  core-never-imports-adapters with a test over `go list` output or `go/build` imports (flags and package usage:
  unverified) and put it in the gate.
- No `init()` side effects and no package-level mutable state.

## Tooling

- Format: `gofmt` (pkg.go.dev/cmd/gofmt); `gofmt -l .` lists files whose formatting differs (the `-l` flag text is truncated
  on the page: confirm with `gofmt -h`). Vet: `go vet ./...` ("reports likely mistakes in packages", pkg.go.dev/cmd/go).
- Static analysis: staticcheck. Install `go install honnef.co/go/tools/cmd/staticcheck@latest`, run `staticcheck ./...`,
  config file `staticcheck.conf` (applies to its subtree) (staticcheck.dev). Pin a version instead of `@latest` in CI;
  look up the current release first (not fetched here). `golangci-lint`: unverified.
- Test: `go test ./...`; `-run <regexp>` selects tests, `-skip` excludes, `-shuffle` randomises order (pkg.go.dev/cmd/go);
  `-race` turns on the race detector (go.dev/doc/articles/race_detector). Table-driven tests with `t.Parallel` are the
  convention.
- Per-file formatter for the hook (case patterns `*.go`): `gofmt -w <file>` (`-w` writes the result back; flag text not fetched: unverified, confirm with `gofmt -h`).

## Strictness from day 0

`go vet ./...` and `staticcheck ./...` in the gate from the first commit; `go test -race ./...`. The `go` directive in
`go.mod` pins the language version; do not lower it. No sentinel `panic` for control flow.

## Logging

`log/slog` (go.dev/blog/slog): `slog.New(slog.NewJSONHandler(os.Stdout, nil))` or `NewTextHandler`. Inject a
`*slog.Logger` into constructors; no package-level logger, no `init`. The handler is the sink:
build it only in `main`. Redaction at the sink with a handler option (`ReplaceAttr`): unverified. Test with a sentinel
secret and a buffer handler.

## Errors

Wrap with `fmt.Errorf("...: %w", err)`; inspect with `errors.Is` / `errors.As`. Wrapping makes the wrapped error part of
your API, so wrap only when callers should be able to inspect it (go.dev/blog/go1.13-errors). Adapters convert vendor
errors into core sentinel or typed errors at the boundary. Never ignore an error; no `panic` outside `main`/tests.

## Config and secrets

One `loadConfig(getenv func(string) string) (Config, error)` in `cmd/<name>`, called once; fail fast; secrets are a type
with a `String()`/`LogValue()` that prints `[redacted]` (`LogValue` / `slog.LogValuer`: unverified); `.env.example`
committed, `.env*` ignored and denied (`templates/snippets.md`). Litmus: 12factor.net/config.

## Gate command

`test -z "$(gofmt -l .)" && go vet ./... && staticcheck ./... && go test -race ./...`
(plus `./scripts/check-secrets.sh` when chosen). Put it in `scripts/gate.sh` or a `Makefile` target.

## CI snippet notes

`actions/setup-go` latest v7.0.0 (`gh api` 2026-11-02); `go-version-file: go.mod` input: unverified. Install staticcheck at
the pinned version, then the gate. Add `go mod verify` / `go mod tidy` diff check: unverified.

## Gotchas

- `go install ...@latest` is not reproducible; pin tool versions in CI.
- `internal/` blocks other modules (layout page); within your module it does not stop an adapter importing another adapter or the core importing an adapter. That needs the boundary test.
- Interfaces belong to the consumer; an interface declared next to its only implementation is not a boundary.
- Wrapping with `%w` is an API commitment; use `%v` to hide the cause.
- `-race` needs cgo on some platforms: unverified.
- Wrapped errors from vendors must not leak into core signatures.
