# TypeScript / Node reference

verified: 2026-11-02
sources: <https://pnpm.io/cli/init>, <https://pnpm.io/workspaces>, <https://nodejs.org/api/packages.html>,
<https://nodejs.org/api/test.html>, <https://www.typescriptlang.org/tsconfig/>,
<https://biomejs.dev/guides/getting-started/>, <https://biomejs.dev/recipes/continuous-integration/>,
<https://typescript-eslint.io/getting-started/>, <https://vitest.dev/guide/>, <https://prettier.io/docs/install>,
npm registry (`registry.npmjs.org/<pkg>/latest`) and `gh api repos/<owner>/<repo>/releases/latest`, both read 2026-11-02.
Lines marked `unverified` have no primary source fetched; re-check before writing them into a project.
Architecture rules: `references/architecture.md`. The global pnpm rule applies (`~/.claude/CLAUDE.md`); do not copy it.

## Init command

These are for the first plan: the `orch:init` skill writes docs and config only and does not run them. CLAUDE.md
`Commands` lists the ones that need code as `not yet runnable (no code)`.

- `pnpm init` creates `package.json`; it fails if one exists (pnpm.io/cli/init). Add `pnpm-workspace.yaml`
  with a `packages:` glob list for a monorepo; internal deps use `workspace:*` (pnpm.io/workspaces).
- Set `"type": "module"` (or `"commonjs"`) explicitly; add `"exports"` for public subpaths (nodejs.org/api/packages.html).
- Node: use the current LTS line from <https://nodejs.org/en/about/previous-releases>. On 2026-11-02
  `nodejs.org/dist/index.json` listed v26.13.0 (Lithium) and v24.22.2 (Krypton) as LTS. Record it in
  `package.json` `engines` (field itself: unverified).

## Layout (interfaces and adapters)

- Single package: `src/core/` (pure logic and the interfaces, imports nothing from adapters), `src/adapters/<thing>/`
  (one folder per implementation), `src/main.ts` (composition root), `test/` or co-located `*.test.ts`.
- Monorepo: `packages/core`, `packages/adapter-<thing>`, `apps/<app>` (composition root). Dependency direction
  `apps -> adapters -> core`; `core` lists no adapter in its `package.json`.
- Interfaces: `interface` / `type` in `core/ports/`, using core types and core errors only, never a vendor type.
- `exports` limits what a package shows, but Node calls it "not a strong boundary" (nodejs.org/api/packages.html):
  enforce direction with a test that reads import specifiers and fails when `core` imports an adapter
  (an architecture test in the server app is enough). Add it to the gate.
- Contract tests: `core/testing/contract.ts` exports `runStoreContract(name, make)`; every adapter and the in-memory
  fake call it (a plugin SDK can ship it as a conformance helper). Fakes live in `core/testing/`.
- Composition root: one `buildApp(config)` in `main.ts`; the context-object pattern (one `AppContext` passed down) is fine.
  Nothing else constructs adapters.

## Tooling

- Package manager: pnpm. Format + lint: **Biome** by default (one tool, one config): `pnpm add -D -E @biomejs/biome`
  (`-E` pins the exact version), `pnpm biome init`, `pnpm biome check --write` locally, `biome ci` in CI
  (biomejs.dev; `ci` has no `--write`).
- Alternative when a framework requires ESLint or type-aware rules: ESLint + typescript-eslint
  (`pnpm add -D eslint @eslint/js typescript typescript-eslint`, flat `eslint.config.mjs`, `pnpm eslint .`;
  typescript-eslint.io) plus Prettier (`pnpm add -D -E prettier`, `prettier . --check`; prettier.io). Use one
  formatter only. Type-aware linting setup: unverified (page not read).
- Type check: `tsc --noEmit` (the `typescript` package ships `tsc`; registry 2026-11-02).
- Tests: **Vitest** (`pnpm add -D vitest`; script `"test": "vitest"`; `vitest run` runs once; needs Node >= 22.12 and
  Vite >= 6.4.0, vitest.dev/guide) or built-in `node --test` (nodejs.org/api/test.html). Single file or name filter
  (`vitest run <file> -t <name>`): unverified.
- Per-file formatter for the hook (`{{formatter}}`, case patterns `*.ts|*.tsx|*.js|*.jsx|*.json`; pick the ones the project uses): `pnpm biome format --write` (path arguments: unverified) or `pnpm prettier --write` (prettier.io/docs/cli, not fetched: unverified).

## Strictness from day 0

`"strict": true` (typescriptlang.org/tsconfig/strict.html; the family grows with TS versions, so pin the TS version).
Also on: `noUncheckedIndexedAccess`, `noImplicitOverride`, `noUnusedLocals`, `verbatimModuleSyntax`
(all listed in typescriptlang.org/tsconfig). No `any` without a comment. Lint level for escape hatches
(`noExplicitAny`, `noNonNullAssertion`) is a Biome rule setting; exact rule names: unverified.

## Logging

`Logger` interface in `core/ports/` (`debug|info|warn|error(msg, fields)`); the sink is an adapter. `pino` is a common
implementation; its API and `redact` option: unverified. Inject the logger; no module-level
logger. Redact by field name at the sink; add a test that logs a sentinel secret and asserts it is absent.

## Errors

Core error classes extend `Error` and keep the cause (`new X(msg, { cause })`, unverified). One base `AppError`
per package; map vendor errors to core errors inside the adapter. Catch-all only in `main.ts`.

## Config and secrets

One `loadConfig(env)` in `main.ts`, parsed once into a typed readonly object (a schema library such as `zod` fits; API unverified), fail fast on a missing key, `.env.example` committed, `.env*` gitignored and denied
(`templates/snippets.md`). Secrets are a branded type whose `toString`/`toJSON` print `[redacted]`.
Litmus: the repo could be public now (12factor.net/config).

## Gate command

`pnpm run gate` = `biome ci . && tsc --noEmit && vitest run && node scripts/check-boundaries.mjs`
(plus `./scripts/check-secrets.sh` when chosen). The same command locally and in CI.

## CI snippet notes

Write `ci.yml` only when the gate command exists after init; with no code yet, put "add CI once the gate exists" into `docs/STATE.md` next steps instead.

Add before the gate step: `pnpm/action-setup` (latest v6.1.0) then `actions/setup-node` (latest v7.1.0), both
via `gh api` 2026-11-02; then `pnpm install --frozen-lockfile` (flag: unverified). `setup-node` `cache: pnpm`
and `node-version-file`: unverified. uv's docs advise pinning third-party actions to a full commit SHA
(docs.astral.sh/uv/guides/integration/github/); consider it for non-GitHub-owned actions.

## Gotchas

- **TypeScript 7 vs typescript-eslint.** Registry 2026-11-02: `typescript@latest` is 7.0.2, while
  `typescript-eslint@8.72.1` declares peer `typescript >=4.8.4 <6.1.0`. ESLint + typescript-eslint therefore cannot take
  the newest TypeScript today: pin TypeScript 6.x, or use Biome. Re-check at write time.
- Biome and Prettier overlap on formatting; never run both.
- `pnpm init` refuses an existing `package.json`. Never use `npm` (global rule).
- Do not rely on `exports` as a boundary; add the import test.
- `workspace:*` deps resolve only inside the workspace; what publishing does with them: unverified.
